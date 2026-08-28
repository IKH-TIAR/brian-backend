from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import case, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
import httpx
import os
import uuid

from database import get_db
from models import AdminCommand, Contact, Conversation, Message, Booking, BookingUnit, Property

router = APIRouter()

@router.get("/commands")
async def list_commands(db: AsyncSession = Depends(get_db)):
    # Explicit columns only — skips the 3 large unused Text columns
    # (ai_system_prompt, template_en, template_es)
    stmt = (
        select(AdminCommand.command, AdminCommand.label, AdminCommand.category,
               AdminCommand.required_params, AdminCommand.is_ai)
        .filter(AdminCommand.is_active == True)
    )
    result = await db.execute(stmt)
    rows = result.all()

    output = {}
    for cmd in rows:
        if cmd.category not in output:
            output[cmd.category] = []
        output[cmd.category].append({
            "command": cmd.command,
            "label": cmd.label,
            "required_params": cmd.required_params,
            "is_ai": cmd.is_ai
        })

    return output

class CommandUpdateRequest(BaseModel):
    label: str
    ai_system_prompt: str | None = None
    template_en: str | None = None
    template_es: str | None = None
    is_active: bool
    set_mode_after: str | None = None

@router.get("/admin/commands")
async def get_all_commands(db: AsyncSession = Depends(get_db)):
    stmt = select(AdminCommand).order_by(AdminCommand.category, AdminCommand.label)
    result = await db.execute(stmt)
    commands = result.scalars().all()
    
    return [
        {
            "id": str(cmd.id),
            "command": cmd.command,
            "label": cmd.label,
            "category": cmd.category,
            "ai_system_prompt": cmd.ai_system_prompt or "",
            "template_en": cmd.template_en or "",
            "template_es": cmd.template_es or "",
            "is_active": cmd.is_active,
            "set_mode_after": cmd.set_mode_after
        }
        for cmd in commands
    ]

@router.put("/admin/commands/{command_id}")
async def update_command(command_id: str, req: CommandUpdateRequest, db: AsyncSession = Depends(get_db)):
    stmt = select(AdminCommand).filter(AdminCommand.id == command_id)
    result = await db.execute(stmt)
    cmd = result.scalar_one_or_none()
    
    if not cmd:
        raise HTTPException(status_code=404, detail="Command not found")
        
    cmd.label = req.label
    cmd.ai_system_prompt = req.ai_system_prompt
    cmd.template_en = req.template_en
    cmd.template_es = req.template_es
    cmd.is_active = req.is_active
    cmd.set_mode_after = req.set_mode_after
    
    await db.commit()
    return {"status": "success"}

class ExecuteCommandRequest(BaseModel):
    command: str
    phone: str
    params: dict = {}

@router.post("/commands/execute")
async def execute_command(req: ExecuteCommandRequest, db: AsyncSession = Depends(get_db)):
    webhook_url = os.getenv("N8N_ADMIN_WEBHOOK_URL")
    if not webhook_url:
        raise HTTPException(status_code=500, detail="N8N_ADMIN_WEBHOOK_URL not configured")
        
    params = dict(req.params or {})

    # Auto-enrich command params from guest's booking if missing/empty
    raw_phone = (req.phone or "").strip()
    clean_phone = raw_phone.lstrip("+")
    phone_variants = list({raw_phone, clean_phone, f"+{clean_phone}"} - {""})

    if phone_variants:
        stmt = (
            select(Booking)
            .join(Contact)
            .filter(
                Contact.phone.in_(phone_variants),
                Booking.status.notin_(["cancelled"])
            )
            .order_by(case((Booking.status == 'pending', 1), else_=0), Booking.created_at.desc())
        )
        res = await db.execute(stmt)
        booking = res.scalars().first()

        if booking:
            if req.command == "balance_due":
                amt_str = str(params.get("amount") or "").strip()
                amt_num = float(amt_str.replace("$", "").replace(",", "") or 0)
                if amt_num <= 0 and booking.balance_due is not None:
                    params["amount"] = f"${float(booking.balance_due):.2f}"

            if req.command == "deposit_received":
                dep_str = str(params.get("deposit_amount") or params.get("amount") or "").strip()
                dep_num = float(dep_str.replace("$", "").replace(",", "") or 0)
                if dep_num <= 0 and booking.deposit_amount is not None:
                    params["deposit_amount"] = float(booking.deposit_amount)
                    params["amount"] = float(booking.deposit_amount)

            if req.command == "final_payment_received":
                pay_str = str(params.get("payment_amount") or params.get("amount") or "").strip()
                pay_num = float(pay_str.replace("$", "").replace(",", "") or 0)
                if pay_num <= 0 and booking.balance_due is not None:
                    params["payment_amount"] = float(booking.balance_due)
                    params["amount"] = float(booking.balance_due)

            if req.command == "pre_arrival_message":
                # Auto-enrich bungalow from booking_units if left blank by admin
                if not params.get("bungalow"):
                    bu_stmt = (
                        select(BookingUnit)
                        .options(selectinload(BookingUnit.property))
                        .filter(BookingUnit.booking_id == booking.id)
                    )
                    bu_res = await db.execute(bu_stmt)
                    units = bu_res.scalars().all()
                    if units:
                        prop_names = [u.property.name for u in units if u.property]
                        if prop_names:
                            params["bungalow"] = ", ".join(prop_names)

    # Validation: Pre-arrival message strictly requires either a booking or an explicit bungalow override
    if req.command == "pre_arrival_message" and not params.get("bungalow"):
        raise HTTPException(
            status_code=404,
            detail=f"No booking found for guest '{req.phone}' and no bungalow was specified. Please create a booking first or enter a bungalow in the override field."
        )

    payload = {
        "command": req.command,
        "phone": req.phone,
        "params": params,
        "source": "admin_panel"
    }
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(webhook_url, json=payload, timeout=30.0)
            response.raise_for_status()
            try:
                return response.json()
            except Exception:
                return {"status": "success", "message": response.text or "Command executed successfully"}
        except httpx.HTTPError as e:
            raise HTTPException(status_code=500, detail=f"Failed to execute command: {str(e)}")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Command execution error: {str(e)}")


class AdminReplyRequest(BaseModel):
    phone: str
    text: str

@router.post("/admin-reply")
async def admin_reply(req: AdminReplyRequest, db: AsyncSession = Depends(get_db)):

    contact_stmt = select(Contact).filter(Contact.phone == req.phone)
    contact_result = await db.execute(contact_stmt)
    contact = contact_result.scalar_one_or_none()
    
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
        
    
    conv_stmt = select(Conversation).filter(Conversation.contact_id == contact.id)
    conv_result = await db.execute(conv_stmt)
    conv = conv_result.scalar_one_or_none()
    
    if not conv:
        
        conv = Conversation(contact_id=contact.id)
        db.add(conv)
        await db.commit()
        await db.refresh(conv)
        
   
    # msg = Message(
    #     conversation_id=conv.id,
    #     role="admin",
    #     content=f"[ADMIN REPLY] {req.text}",
    #     status="sent"
    # )
    # db.add(msg)
    # await db.commit()
    
    webhook_url = os.getenv("N8N_MAIN_WEBHOOK_URL")
    if not webhook_url:
        raise HTTPException(status_code=500, detail="N8N_MAIN_WEBHOOK_URL is not configured")

    payload = {
        "source": "test_interface",
        "messages": [
            {
                "from": "50689494045", 
                "type": "text",
                "text": {
                    "body": f"!reply {req.phone} {req.text}"
                }
            }
        ]
    }
    async with httpx.AsyncClient() as client:
        try:
            res = await client.post(webhook_url, json=payload, timeout=20.0)
            res.raise_for_status()
        except httpx.HTTPError as e:
            raise HTTPException(status_code=500, detail=f"Failed to deliver admin reply to n8n: {str(e)}")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error sending admin reply: {str(e)}")
    
    return {"status": "success", "message": "Admin reply logged and sent to n8n."}

class AdminResetRequest(BaseModel):
    phone: str

@router.post("/admin-reset")
async def admin_reset(req: AdminResetRequest, db: AsyncSession = Depends(get_db)):
    # 1. Delete all messages for this contact from local database
    raw_phone = (req.phone or "").strip()
    clean_phone = raw_phone.lstrip("+")
    phone_variants = list({raw_phone, clean_phone, f"+{clean_phone}"} - {""})

    if phone_variants:
        stmt = (
            select(Conversation)
            .join(Contact)
            .filter(Contact.phone.in_(phone_variants))
        )
        res = await db.execute(stmt)
        conv = res.scalars().first()

        if conv:
            await db.execute(
                text("DELETE FROM messages WHERE conversation_id = :cid"),
                {"cid": str(conv.id)}
            )
            await db.commit()

    # 2. Trigger n8n memory reset webhook
    webhook_url = os.getenv("N8N_MAIN_WEBHOOK_URL")
    if webhook_url:
        payload = {
            "source": "test_interface",
            "messages": [
                {
                    "from": "50689494045", 
                    "type": "text",
                    "text": {
                        "body": f"!reset {req.phone}"
                    }
                }
            ]
        }
        async with httpx.AsyncClient() as client:
            try:
                await client.post(webhook_url, json=payload, timeout=10.0)
            except Exception as e:
                print(f"Failed to trigger n8n reset webhook: {e}")

    return {"status": "success", "message": "Chat history cleared from database and n8n reset."}
