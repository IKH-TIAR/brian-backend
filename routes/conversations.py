from datetime import datetime, date, timezone
from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from sqlalchemy import or_, desc, text
import httpx
import os

import uuid
from database import get_db, async_session_maker
from models import Conversation, Contact, Message, Booking, Property, BookingUnit

router = APIRouter()

CONVERSATIONS_PER_PAGE = 50

@router.get("/conversations")
async def list_conversations(
    search: str = Query(None, min_length=1),
    limit: int = Query(CONVERSATIONS_PER_PAGE, ge=1, le=200),
    before: str = Query(None, description="Keyset cursor: '<last_message_at ISO>|<conversation_id>' of the last row"),
    db: AsyncSession = Depends(get_db)
):
    from sqlalchemy import text

    search_filter = ""
    cursor_filter = ""
    params: dict = {"lim": limit + 1}

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

    if before and "|" in before:
        before_ts, before_id = before.split("|", 1)
        cursor_filter = """
            AND (
                COALESCE(c.last_message_at, c.created_at) < :before_ts
                OR (
                    COALESCE(c.last_message_at, c.created_at) = :before_ts
                    AND c.id < :before_id::uuid
                )
            )
        """
        params["before_ts"] = before_ts
        params["before_id"] = before_id

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

            -- Latest message content + role (index-only scan via ix_messages_conv_created)
            lm.content AS latest_message,
            lm.role AS latest_message_role,

            -- Unread count (uses ix_messages_unread partial index)
            (
                SELECT COUNT(*) FROM messages m
                WHERE m.conversation_id = c.id
                  AND m.role = 'user' AND m.is_read = FALSE
            ) AS unread_count,

            -- Escalation: relevant only when mode = HUMAN
            CASE
                WHEN ct.mode = 'HUMAN' AND esc.escalation_reason IS NOT NULL THEN TRUE
                ELSE FALSE
            END AS is_escalated,
            esc.escalation_reason

        FROM conversations c
        JOIN contacts ct ON ct.id = c.contact_id
        LEFT JOIN LATERAL (
            SELECT content, role
            FROM messages m2
            WHERE m2.conversation_id = c.id
            ORDER BY m2.created_at DESC
            LIMIT 1
        ) lm ON TRUE
        LEFT JOIN LATERAL (
            SELECT escalation_reason
            FROM messages m3
            WHERE m3.conversation_id = c.id
              AND m3.escalated = TRUE
            ORDER BY m3.created_at DESC
            LIMIT 1
        ) esc ON TRUE
        WHERE 1=1
        {search_filter}
        {cursor_filter}
        ORDER BY COALESCE(c.last_message_at, c.created_at) DESC NULLS LAST, c.id DESC
        LIMIT :lim
    """)

    result = await db.execute(sql, params)
    rows = result.mappings().all()

    has_more = len(rows) > limit
    rows = rows[:limit]

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

    return {"conversations": output, "has_more": has_more}


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
    newest_msg_ts = msgs_raw[0]["created_at"] if msgs_raw else None
    msgs_raw.reverse()  # back to chronological order for the frontend

    # Query 3: Fetch media records for this phone number from whatsapp_media
    # (matches phone regardless of formatting or '+' prefix)
    raw_phone = (phone or "").strip()
    clean_digits = "".join(filter(str.isdigit, raw_phone))
    clean_phone = raw_phone.lstrip("+")
    plus_phone = f"+{clean_phone}"

    media_params = {
        "raw_phone": raw_phone,
        "clean_phone": clean_phone,
        "plus_phone": plus_phone,
        "clean_digits": clean_digits,
        "lim": 500
    }
    media_result = await db.execute(
        text("""
            SELECT id, media_id, mime_type, caption, file_size, created_at
            FROM whatsapp_media
            WHERE (
                phone = :raw_phone 
                OR phone = :clean_phone 
                OR phone = :plus_phone 
                OR phone = :clean_digits
                OR regexp_replace(phone, '\\D', '', 'g') = :clean_digits
            )
            ORDER BY created_at ASC
            LIMIT :lim
        """),
        media_params
    )
    media_rows = list(media_result.mappings().all())

    def _to_timestamp(dt):
        if dt is None:
            return 0
        if dt.tzinfo is not None:
            return dt.timestamp()
        return dt.replace(tzinfo=timezone.utc).timestamp()

    formatted_messages = []
    used_media_ids = set()

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

        content_raw = (m["content"] or "").strip()
        content_lower = content_raw.lower()
        is_image_msg = (
            content_lower in ("image", "photo", "media", "picture", "[image]", "[photo]", "[media]", "[picture]", "[image received]", "[photo received]")
            or content_lower.startswith("[image")
            or content_lower.startswith("[photo")
            or content_lower.startswith("[media")
            or content_lower.startswith("image/")
        )

        if is_image_msg and media_rows:
            # Find the best matching media record based on closest creation timestamp
            msg_ts = _to_timestamp(m["created_at"])
            best_med = None
            min_diff = None
            for med in media_rows:
                if med["id"] in used_media_ids:
                    continue
                diff = abs(_to_timestamp(med["created_at"]) - msg_ts)
                if min_diff is None or diff < min_diff:
                    min_diff = diff
                    best_med = med

            if best_med:
                used_media_ids.add(best_med["id"])
                msg_dict["media_url"] = f"/api/media/{best_med['id']}"
                msg_dict["mime_type"] = best_med["mime_type"]
                if best_med["caption"]:
                    msg_dict["caption"] = best_med["caption"]
            else:
                # Fallback to the latest media record if all have been claimed
                med = media_rows[-1]
                msg_dict["media_url"] = f"/api/media/{med['id']}"
                msg_dict["mime_type"] = med["mime_type"]
                if med["caption"]:
                    msg_dict["caption"] = med["caption"]

        formatted_messages.append(msg_dict)

    # Fire is_read update in the background — response is returned immediately
    background_tasks.add_task(_mark_messages_read, conv_id)

    # Query 4: Fetch latest non-pending booking for this contact
    booking_data = None
    booking_result = await db.execute(
        text("""
            SELECT b.id, b.status, b.check_in, b.check_out, b.guest_count,
                   b.has_pets, b.guest_name, b.total_amount, b.deposit_amount,
                   b.refundable_deposit, b.final_payment_amount, b.balance_due, b.payment_due_date,
                   b.deposit_due_date, b.currency
            FROM bookings b
            WHERE b.contact_id = (SELECT id FROM contacts WHERE phone = :phone)
              AND b.status NOT IN ('pending')
            ORDER BY b.created_at DESC
            LIMIT 1
        """),
        {"phone": phone}
    )
    booking_row = booking_result.mappings().one_or_none()

    if booking_row:
        # Get property/bungalow names from booking_units
        units_result = await db.execute(
            text("""
                SELECT bu.unit_name_snapshot, p.name AS property_name
                FROM booking_units bu
                LEFT JOIN properties p ON p.id = bu.property_id
                WHERE bu.booking_id = :bid
            """),
            {"bid": str(booking_row["id"])}
        )
        units = units_result.mappings().all()
        bungalow_names = [
            u["unit_name_snapshot"] or u["property_name"] or "Unknown"
            for u in units
        ]

        booking_data = {
            "id": str(booking_row["id"]),
            "status": booking_row["status"],
            "check_in": booking_row["check_in"].isoformat() if booking_row["check_in"] else None,
            "check_out": booking_row["check_out"].isoformat() if booking_row["check_out"] else None,
            "guest_count": booking_row["guest_count"],
            "has_pets": booking_row["has_pets"],
            "guest_name": booking_row["guest_name"],
            "total_amount": float(booking_row["total_amount"] or 0),
            "deposit_amount": float(booking_row["deposit_amount"] or 0),
            "refundable_deposit": float(booking_row["refundable_deposit"] or 0),
            "final_payment_amount": float(booking_row["final_payment_amount"] or 0),
            "balance_due": float(booking_row["balance_due"] or 0),
            "payment_due_date": booking_row["payment_due_date"].isoformat() if booking_row["payment_due_date"] else None,
            "deposit_due_date": booking_row["deposit_due_date"].isoformat() if booking_row["deposit_due_date"] else None,
            "currency": booking_row["currency"] or "USD",
            "bungalows": bungalow_names
        }

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
        "booking": booking_data,
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
        # Sync name to all non-cancelled bookings for this contact
        bookings_result = await db.execute(
            select(Booking).filter(
                Booking.contact_id == contact.id,
                Booking.status.notin_(["cancelled"])
            )
        )
        for booking in bookings_result.scalars().all():
            booking.guest_name = update_data.name
            if update_data.name:
                parts = update_data.name.strip().split()
                booking.guest_first_name = parts[0] if parts else ""
    if update_data.is_returning is not None:
        contact.is_returning = update_data.is_returning
        
    await db.commit()
    return {"status": "success"}

@router.delete("/contacts/{phone}")
async def delete_contact(phone: str, db: AsyncSession = Depends(get_db)):
    raw_phone = (phone or "").strip()
    clean_phone = raw_phone.lstrip("+")
    clean_digits = "".join(filter(str.isdigit, raw_phone))
    phone_variants = list({raw_phone, clean_phone, f"+{clean_phone}", clean_digits} - {""})

    # 1. Find the contact
    stmt = select(Contact).filter(Contact.phone.in_(phone_variants))
    res = await db.execute(stmt)
    contact = res.scalars().first()
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")

    contact_id = contact.id

    # 2. Delete all Booking Units and Bookings
    bk_stmt = select(Booking).options(selectinload(Booking.booking_units)).filter(Booking.contact_id == contact_id)
    bk_res = await db.execute(bk_stmt)
    bookings = bk_res.scalars().all()
    for b in bookings:
        if b.booking_units:
            for u in b.booking_units:
                await db.delete(u)
        await db.delete(b)
    await db.flush()

    # 3. Find and delete all Messages and Conversations
    conv_stmt = select(Conversation).filter(Conversation.contact_id == contact_id)
    conv_res = await db.execute(conv_stmt)
    conversations = conv_res.scalars().all()
    for conv in conversations:
        await db.execute(
            text("DELETE FROM messages WHERE conversation_id = :cid"),
            {"cid": str(conv.id)}
        )
        await db.delete(conv)
    await db.flush()

    # 4. Clean up media and template send records associated with this phone
    try:
        await db.execute(
            text("""
                DELETE FROM whatsapp_media 
                WHERE phone = :p1 OR phone = :p2 OR phone = :p3 OR phone = :p4 
                   OR regexp_replace(phone, '\\D', '', 'g') = :p4
            """),
            {"p1": raw_phone, "p2": clean_phone, "p3": f"+{clean_phone}", "p4": clean_digits}
        )
    except Exception as e:
        print(f"whatsapp_media delete skipped: {e}")

    try:
        await db.execute(
            text("""
                DELETE FROM whatsapp_template_sends 
                WHERE phone = :p1 OR phone = :p2 OR phone = :p3 OR phone = :p4
                   OR regexp_replace(phone, '\\D', '', 'g') = :p4
            """),
            {"p1": raw_phone, "p2": clean_phone, "p3": f"+{clean_phone}", "p4": clean_digits}
        )
    except Exception as e:
        print(f"whatsapp_template_sends delete skipped: {e}")

    # 5. Delete the Contact record
    await db.delete(contact)
    await db.commit()

    # 6. Trigger n8n memory reset asynchronously in the background
    webhook_url = os.getenv("N8N_MAIN_WEBHOOK_URL")
    if webhook_url:
        payload = {
            "source": "test_interface",
            "messages": [
                {
                    "from": "50689494045", 
                    "type": "text",
                    "text": {
                        "body": f"!reset {phone}"
                    }
                }
            ]
        }
        try:
            async with httpx.AsyncClient() as client:
                await client.post(webhook_url, json=payload, timeout=5.0)
        except Exception as e:
            print(f"n8n reset trigger on contact delete skipped: {e}")

    return {"status": "success", "message": f"Contact '{phone}' and all related data deleted successfully"}

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

    # Also synchronize with the active booking if one exists
    bk_stmt = (
        select(Booking)
        .filter(
            or_(Booking.conversation_id == conv.id, Booking.contact_id == conv.contact_id),
            Booking.status.notin_(["cancelled"])
        )
        .order_by(Booking.created_at.desc())
        .limit(1)
    )
    bk_res = await db.execute(bk_stmt)
    active_bk = bk_res.scalars().first()
    if active_bk:
        if update_data.check_in is not None:
            active_bk.check_in = update_data.check_in
        if update_data.check_out is not None:
            active_bk.check_out = update_data.check_out
        if update_data.name is not None:
            active_bk.guest_name = update_data.name
            if not active_bk.guest_first_name and update_data.name:
                active_bk.guest_first_name = update_data.name.split()[0]
        if update_data.bungalow:
            # Match property by name or code and update booking unit
            prop_query = update_data.bungalow.strip()
            prop_stmt = select(Property).filter(
                or_(
                    Property.name.ilike(f"%{prop_query}%"),
                    Property.code.ilike(f"%{prop_query}%")
                )
            ).limit(1)
            prop_res = await db.execute(prop_stmt)
            matched_prop = prop_res.scalar_one_or_none()
            if matched_prop:
                bu_stmt = select(BookingUnit).filter(BookingUnit.booking_id == active_bk.id).limit(1)
                bu_res = await db.execute(bu_stmt)
                bu = bu_res.scalar_one_or_none()
                if bu:
                    bu.property_id = matched_prop.id
                else:
                    db.add(BookingUnit(id=uuid.uuid4(), booking_id=active_bk.id, property_id=matched_prop.id))
        
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
    contact.mode_updated_at = datetime.now()
    
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
    contact.mode_updated_at = datetime.now()
    
    await db.commit()
    return {"status": "success"}
