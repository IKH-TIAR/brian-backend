from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from sqlalchemy import or_, desc, text
import httpx
import os

from database import get_db, async_session_maker
from models import Conversation, Contact, Message

router = APIRouter()

@router.get("/conversations")
async def list_conversations(
    search: str = Query(None, min_length=1),
    db: AsyncSession = Depends(get_db)
):
    from sqlalchemy import text

    search_filter = ""
    params: dict = {}

    if search:
        search_filter = """
            AND (
                ct.phone ILIKE :search
                OR ct.name ILIKE :search
                OR EXISTS (
                    SELECT 1 FROM messages sm
                    WHERE sm.conversation_id = c.id
                    AND sm.content ILIKE :search
                )
            )
        """
        params["search"] = f"%{search}%"

    sql = text(f"""
        SELECT
            c.id                                                        AS conversation_id,
            ct.phone,
            ct.name,
            ct.is_returning,
            ct.mode,
            ct.mode_reason,
            c.bungalow,
            c.check_in,
            c.check_out,
            c.last_message_at,

            -- Latest message content + role in one pass
            (
                SELECT m2.content FROM messages m2
                WHERE m2.conversation_id = c.id
                ORDER BY m2.created_at DESC LIMIT 1
            ) AS latest_message,
            (
                SELECT m2.role FROM messages m2
                WHERE m2.conversation_id = c.id
                ORDER BY m2.created_at DESC LIMIT 1
            ) AS latest_message_role,

            -- Unread count (user messages not yet marked is_read)
            COUNT(m.id) FILTER (
                WHERE m.role = 'user' AND m.is_read = FALSE
            ) AS unread_count,

            -- Escalation: only relevant when mode = HUMAN
            BOOL_OR(m.escalated) FILTER (
                WHERE ct.mode = 'HUMAN'
            ) AS is_escalated,
            (
                SELECT m3.escalation_reason FROM messages m3
                WHERE m3.conversation_id = c.id
                  AND m3.escalated = TRUE
                ORDER BY m3.created_at DESC LIMIT 1
            ) AS escalation_reason

        FROM conversations c
        JOIN contacts ct ON ct.id = c.contact_id
        LEFT JOIN messages m ON m.conversation_id = c.id
        WHERE 1=1
        {search_filter}
        GROUP BY c.id, ct.id
        ORDER BY c.last_message_at DESC NULLS LAST
    """)

    result = await db.execute(sql, params)
    rows = result.mappings().all()

    output = []
    for row in rows:
        output.append({
            "conversation_id": str(row["conversation_id"]),
            "phone": row["phone"],
            "name": row["name"],
            "is_returning": row["is_returning"],
            "mode": row["mode"],
            "mode_reason": row["mode_reason"],
            "bungalow": row["bungalow"],
            "check_in": row["check_in"].isoformat() if row["check_in"] else None,
            "check_out": row["check_out"].isoformat() if row["check_out"] else None,
            "last_message_at": row["last_message_at"].isoformat() if row["last_message_at"] else None,
            "latest_message": row["latest_message"] or "",
            "latest_message_role": row["latest_message_role"] or "",
            "unread_count": int(row["unread_count"] or 0),
            "is_escalated": bool(row["is_escalated"]),
            "escalation_reason": row["escalation_reason"],
        })

    return output


async def _mark_messages_read(conversation_id: str):
    """Background task: mark all unread user messages as read.
    Runs AFTER the response is already sent — never blocks the client."""
    async with async_session_maker() as session:
        await session.execute(
            text("UPDATE messages SET is_read = TRUE WHERE conversation_id = :cid AND role = 'user' AND is_read = FALSE"),
            {"cid": conversation_id}
        )
        await session.commit()


MESSAGES_PER_PAGE = 100

@router.get("/conversations/{phone}")
async def get_conversation_thread(
    phone: str,
    background_tasks: BackgroundTasks,
    before: str = Query(None, description="Load messages older than this message ID (for pagination)"),
    db: AsyncSession = Depends(get_db)
):
    # Query 1: contact + conversation in a single JOIN
    row = await db.execute(
        text("""
            SELECT
                ct.phone, ct.name, ct.mode, ct.is_returning,
                c.id AS conv_id, c.bungalow, c.check_in, c.check_out, c.payment_due_date
            FROM contacts ct
            JOIN conversations c ON c.contact_id = ct.id
            WHERE ct.phone = :phone
            LIMIT 1
        """),
        {"phone": phone}
    )
    result = row.mappings().one_or_none()

    if not result:
        # Check if contact exists at all
        ct = await db.execute(text("SELECT phone, name, mode FROM contacts WHERE phone = :p"), {"p": phone})
        ct_row = ct.mappings().one_or_none()
        if not ct_row:
            raise HTTPException(status_code=404, detail="Contact not found")
        return {"contact": dict(ct_row), "conversation": None, "messages": [], "has_more": False}

    conv_id = str(result["conv_id"])

    # Query 2: last N messages (paginated), newest-first so LIMIT works correctly, then reversed for display
    if before:
        # Cursor-based: get messages older than the given message ID
        msgs_result = await db.execute(
            text("""
                SELECT id, role, content, created_at, escalated, escalation_reason
                FROM messages
                WHERE conversation_id = :cid
                  AND created_at < (
                      SELECT created_at FROM messages WHERE id = :before_id
                  )
                ORDER BY created_at DESC
                LIMIT :lim
            """),
            {"cid": conv_id, "before_id": before, "lim": MESSAGES_PER_PAGE + 1}
        )
    else:
        msgs_result = await db.execute(
            text("""
                SELECT id, role, content, created_at, escalated, escalation_reason
                FROM messages
                WHERE conversation_id = :cid
                ORDER BY created_at DESC
                LIMIT :lim
            """),
            {"cid": conv_id, "lim": MESSAGES_PER_PAGE + 1}
        )

    msgs_raw = msgs_result.mappings().all()

    # If we got one extra, there are older messages available
    has_more = len(msgs_raw) > MESSAGES_PER_PAGE
    msgs_raw = list(msgs_raw[:MESSAGES_PER_PAGE])  # trim the extra
    msgs_raw.reverse()  # back to chronological order for the frontend

    # Query 3: Fetch media records for this phone number from whatsapp_media
    media_result = await db.execute(
        text("""
            SELECT id, media_id, mime_type, caption, file_size, created_at
            FROM whatsapp_media
            WHERE phone = :phone
            ORDER BY created_at ASC
        """),
        {"phone": phone}
    )
    media_rows = list(media_result.mappings().all())

    formatted_messages = []
    media_idx = 0

    for m in msgs_raw:
        msg_dict = {
            "id": str(m["id"]),
            "role": m["role"],
            "content": m["content"],
            "created_at": m["created_at"].isoformat() if m["created_at"] else None,
            "escalated": m["escalated"],
            "escalation_reason": m["escalation_reason"],
            "media_url": None,
            "mime_type": None,
            "caption": None
        }

        content_lower = (m["content"] or "").lower()
        is_image_msg = ("image" in content_lower or "photo" in content_lower or "media" in content_lower or "picture" in content_lower)

        if is_image_msg and media_idx < len(media_rows):
            med = media_rows[media_idx]
            msg_dict["media_url"] = f"/api/media/{med['id']}"
            msg_dict["mime_type"] = med["mime_type"]
            if med["caption"]:
                msg_dict["caption"] = med["caption"]
            media_idx += 1
        elif is_image_msg and len(media_rows) > 0:
            med = media_rows[-1]
            msg_dict["media_url"] = f"/api/media/{med['id']}"
            msg_dict["mime_type"] = med["mime_type"]
            if med["caption"]:
                msg_dict["caption"] = med["caption"]

        formatted_messages.append(msg_dict)

    # Fire is_read update in the background — response is returned immediately
    background_tasks.add_task(_mark_messages_read, conv_id)

    return {
        "contact": {
            "phone": result["phone"],
            "name": result["name"],
            "mode": result["mode"],
            "is_returning": result["is_returning"]
        },
        "conversation": {
            "id": conv_id,
            "bungalow": result["bungalow"],
            "check_in": result["check_in"],
            "check_out": result["check_out"],
            "payment_due_date": result["payment_due_date"]
        },
        "messages": formatted_messages,
        "has_more": has_more
    }


from pydantic import BaseModel
class ContactUpdate(BaseModel):
    name: str = None
    is_returning: bool = None

@router.patch("/contacts/{phone}")
async def update_contact(phone: str, update_data: ContactUpdate, db: AsyncSession = Depends(get_db)):
    stmt = select(Contact).filter(Contact.phone == phone)
    result = await db.execute(stmt)
    contact = result.scalar_one_or_none()
    
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
        
    if update_data.name is not None:
        contact.name = update_data.name
    if update_data.is_returning is not None:
        contact.is_returning = update_data.is_returning
        
    await db.commit()
    return {"status": "success"}

from datetime import date
from typing import Optional

class BookingUpdate(BaseModel):
    name: Optional[str] = None
    bungalow: Optional[str] = None
    check_in: Optional[date] = None
    check_out: Optional[date] = None

@router.patch("/conversations/{conversation_id}/booking")
async def update_booking(conversation_id: str, update_data: BookingUpdate, db: AsyncSession = Depends(get_db)):
    stmt = select(Conversation).options(selectinload(Conversation.contact)).filter(Conversation.id == conversation_id)
    result = await db.execute(stmt)
    conv = result.scalar_one_or_none()
    
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
        
    if update_data.bungalow is not None:
        conv.bungalow = update_data.bungalow if update_data.bungalow else None
    if update_data.check_in is not None:
        conv.check_in = update_data.check_in
    if update_data.check_out is not None:
        conv.check_out = update_data.check_out
        
    if update_data.name is not None and conv.contact:
        conv.contact.name = update_data.name if update_data.name else None
        
    await db.commit()
    return {"status": "success"}

class ModeUpdate(BaseModel):
    mode: str
    reason: str = None

@router.patch("/contacts/{phone}/mode")
async def update_contact_mode(phone: str, update_data: ModeUpdate, db: AsyncSession = Depends(get_db)):
    if update_data.mode not in ["BOT", "HUMAN"]:
        raise HTTPException(status_code=400, detail="Invalid mode")
        
    stmt = select(Contact).filter(Contact.phone == phone)
    result = await db.execute(stmt)
    contact = result.scalar_one_or_none()
    
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
        
    contact.mode = update_data.mode
    contact.mode_reason = update_data.reason or ("Manual toggle via dashboard")
    
    await db.commit()
    
   
    
    return {"status": "success", "new_mode": contact.mode}


@router.patch("/escalations/{phone}/resolve")
async def resolve_escalation(phone: str, db: AsyncSession = Depends(get_db)):
    
    stmt = select(Contact).filter(Contact.phone == phone)
    result = await db.execute(stmt)
    contact = result.scalar_one_or_none()
    
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
        
    contact.mode = "BOT"
    contact.mode_reason = "Escalation resolved via dashboard"
    
    await db.commit()
    return {"status": "success"}
