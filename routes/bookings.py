import uuid
from datetime import date, time, datetime
from decimal import Decimal
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select, or_, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database import get_db
from models import Booking, BookingUnit, Contact, Conversation, Property, PricingSetting

router = APIRouter()

# --- Pydantic Schemas ---

class BookingUnitCreate(BaseModel):
    property_id: Optional[str] = None
    unit_name_snapshot: Optional[str] = None
    accommodation_amount: Optional[Decimal] = Field(Decimal("0.00"), ge=0)
    cleaning_fee: Optional[Decimal] = Field(Decimal("0.00"), ge=0)
    pet_fee: Optional[Decimal] = Field(Decimal("0.00"), ge=0)
    discount_amount: Optional[Decimal] = Field(Decimal("0.00"), ge=0)
    unit_total: Optional[Decimal] = Field(Decimal("0.00"), ge=0)
    pricing_snapshot: Optional[dict] = None

class BookingCreate(BaseModel):
    reservation_reference: Optional[str] = None
    contact_id: Optional[str] = None
    phone: Optional[str] = None
    conversation_id: Optional[str] = None
    source: Optional[str] = "direct"
    status: str = "pending"
    check_in: Optional[date] = None
    check_out: Optional[date] = None
    checkout_time: Optional[str] = "11:00:00"
    guest_count: Optional[int] = Field(1, ge=1)
    has_pets: bool = False
    guest_name: Optional[str] = None
    guest_first_name: Optional[str] = None
    language_tag: Optional[str] = "english"
    currency: Optional[str] = "USD"
    total_amount: Optional[Decimal] = Field(Decimal("0.00"), ge=0)
    deposit_amount: Optional[Decimal] = Field(Decimal("0.00"), ge=0)
    refundable_deposit: Optional[Decimal] = Field(Decimal("0.00"), ge=0)
    final_payment_amount: Optional[Decimal] = Field(Decimal("0.00"), ge=0)
    balance_due: Optional[Decimal] = Field(None, ge=0)
    deposit_due_date: Optional[date] = None
    payment_due_date: Optional[date] = None
    pricing_snapshot: Optional[dict] = None
    internal_notes: Optional[str] = None
    units: List[BookingUnitCreate] = []

class BookingUpdate(BaseModel):
    reservation_reference: Optional[str] = None
    source: Optional[str] = None
    check_in: Optional[date] = None
    check_out: Optional[date] = None
    checkout_time: Optional[str] = None
    guest_count: Optional[int] = Field(None, ge=1)
    has_pets: Optional[bool] = None
    guest_name: Optional[str] = None
    guest_first_name: Optional[str] = None
    language_tag: Optional[str] = None
    currency: Optional[str] = None
    total_amount: Optional[Decimal] = Field(None, ge=0)
    deposit_amount: Optional[Decimal] = Field(None, ge=0)
    refundable_deposit: Optional[Decimal] = Field(None, ge=0)
    final_payment_amount: Optional[Decimal] = Field(None, ge=0)
    balance_due: Optional[Decimal] = Field(None, ge=0)
    deposit_due_date: Optional[date] = None
    payment_due_date: Optional[date] = None
    internal_notes: Optional[str] = None
    units: Optional[List[BookingUnitCreate]] = None

class StatusTransitionRequest(BaseModel):
    status: str


class ConfirmDepositRequest(BaseModel):
    phone: Optional[str] = None
    booking_id: Optional[str] = None
    deposit_amount: Decimal = Field(..., ge=0)
    currency: Optional[str] = "USD"
    payment_due_date: Optional[date] = None

# Helper to format booking dictionary
def _format_booking(b: Booking) -> dict:
    units_out = []
    if b.booking_units:
        for u in b.booking_units:
            units_out.append({
                "id": str(u.id),
                "property_id": str(u.property_id),
                "property_name": u.property.name if u.property else None,
                "unit_name_snapshot": u.unit_name_snapshot,
                "accommodation_amount": float(u.accommodation_amount or 0),
                "cleaning_fee": float(u.cleaning_fee or 0),
                "pet_fee": float(u.pet_fee or 0),
                "discount_amount": float(u.discount_amount or 0),
                "unit_total": float(u.unit_total or 0),
                "pricing_snapshot": u.pricing_snapshot or {}
            })

    return {
        "id": str(b.id),
        "reservation_reference": b.reservation_reference,
        "contact_id": str(b.contact_id),
        "conversation_id": str(b.conversation_id) if b.conversation_id else None,
        "contact": {
            "id": str(b.contact.id),
            "phone": b.contact.phone,
            "name": b.contact.name,
            "mode": b.contact.mode
        } if b.contact else None,
        "source": b.source,
        "status": b.status,
        "check_in": b.check_in.isoformat() if b.check_in else None,
        "check_out": b.check_out.isoformat() if b.check_out else None,
        "checkout_time": str(b.checkout_time) if b.checkout_time else "11:00:00",
        "guest_count": b.guest_count,
        "has_pets": b.has_pets,
        "guest_name": b.guest_name,
        "guest_first_name": b.guest_first_name,
        "language_tag": b.language_tag,
        "currency": b.currency,
        "total_amount": float(b.total_amount or 0),
        "deposit_amount": float(b.deposit_amount or 0),
        "refundable_deposit": float(b.refundable_deposit or 0),
        "final_payment_amount": float(b.final_payment_amount or 0),
        "balance_due": float(b.balance_due or 0),
        "deposit_due_date": b.deposit_due_date.isoformat() if b.deposit_due_date else None,
        "payment_due_date": b.payment_due_date.isoformat() if b.payment_due_date else None,
        "pricing_snapshot": b.pricing_snapshot or {},
        "internal_notes": b.internal_notes,
        "confirmed_at": b.confirmed_at.isoformat() if b.confirmed_at else None,
        "checked_in_at": b.checked_in_at.isoformat() if b.checked_in_at else None,
        "completed_at": b.completed_at.isoformat() if b.completed_at else None,
        "cancelled_at": b.cancelled_at.isoformat() if b.cancelled_at else None,
        "created_at": b.created_at.isoformat() if b.created_at else None,
        "updated_at": b.updated_at.isoformat() if b.updated_at else None,
        "units": units_out
    }

# ==================================================
# ENDPOINTS
# ==================================================

@router.get("/admin/bookings")
async def list_bookings(
    status: Optional[str] = Query(None, description="Filter by booking status"),
    search: Optional[str] = Query(None, description="Search guest name, ref, or phone"),
    check_in_from: Optional[date] = Query(None, description="Check-in on or after date"),
    check_in_to: Optional[date] = Query(None, description="Check-in on or before date"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db)
):
    stmt = (
        select(Booking)
        .options(
            selectinload(Booking.contact),
            selectinload(Booking.booking_units).selectinload(BookingUnit.property)
        )
        .order_by(Booking.created_at.desc())
    )

    if status:
        stmt = stmt.filter(Booking.status == status)

    if check_in_from:
        stmt = stmt.filter(Booking.check_in >= check_in_from)

    if check_in_to:
        stmt = stmt.filter(Booking.check_in <= check_in_to)

    if search:
        search_pattern = f"%{search.strip()}%"
        stmt = stmt.outerjoin(Contact, Booking.contact_id == Contact.id).filter(
            or_(
                Booking.reservation_reference.ilike(search_pattern),
                Booking.guest_name.ilike(search_pattern),
                Booking.guest_first_name.ilike(search_pattern),
                Contact.name.ilike(search_pattern),
                Contact.phone.ilike(search_pattern)
            )
        )

    stmt = stmt.limit(limit).offset(offset)
    result = await db.execute(stmt)
    bookings = result.scalars().all()

    return [_format_booking(b) for b in bookings]


@router.get("/admin/bookings/{booking_id}")
async def get_booking(booking_id: str, db: AsyncSession = Depends(get_db)):
    stmt = (
        select(Booking)
        .options(
            selectinload(Booking.contact),
            selectinload(Booking.booking_units).selectinload(BookingUnit.property)
        )
        .filter(Booking.id == booking_id)
    )
    res = await db.execute(stmt)
    booking = res.scalar_one_or_none()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")

    return _format_booking(booking)


@router.post("/admin/bookings")
async def create_booking(req: BookingCreate, db: AsyncSession = Depends(get_db)):
    contact = None
    if req.contact_id:
        contact_res = await db.execute(select(Contact).filter(Contact.id == req.contact_id))
        contact = contact_res.scalar_one_or_none()
        if not contact:
            raise HTTPException(status_code=404, detail="Contact not found")
    elif req.phone:
        raw_phone = req.phone.strip()
        clean_phone = raw_phone.lstrip("+")
        clean_digits = "".join(filter(str.isdigit, raw_phone))
        phone_variants = list({raw_phone, clean_phone, f"+{clean_phone}", clean_digits} - {""})
        
        contact_res = await db.execute(select(Contact).filter(Contact.phone.in_(phone_variants)))
        contact = contact_res.scalars().first()
        if not contact:
            raise HTTPException(
                status_code=404,
                detail="Contact not found. Bookings can only be created for existing contacts."
            )
        elif req.guest_name and (not contact.name or contact.name.lower() in ("unknown", "unknown guest")):
            contact.name = req.guest_name
    else:
        raise HTTPException(status_code=400, detail="Either contact_id or phone must be provided")

    # Find or create Conversation if conversation_id not explicitly given
    conv_id = req.conversation_id
    if not conv_id:
        conv_stmt = select(Conversation).filter(Conversation.contact_id == contact.id).limit(1)
        conv_res = await db.execute(conv_stmt)
        conv = conv_res.scalar_one_or_none()
        if not conv:
            conv = Conversation(
                id=uuid.uuid4(),
                contact_id=contact.id
            )
            db.add(conv)
            await db.flush()
        conv_id = str(conv.id)

    # Parse checkout_time string to time object if needed
    co_time = None
    if req.checkout_time:
        try:
            parts = [int(p) for p in req.checkout_time.split(":")]
            co_time = time(parts[0], parts[1], parts[2] if len(parts) > 2 else 0)
        except Exception:
            co_time = time(11, 0, 0)
    else:
        co_time = time(11, 0, 0)

    # Auto extract guest_first_name if missing
    g_first_name = req.guest_first_name
    if not g_first_name and (req.guest_name or contact.name):
        full_nm = (req.guest_name or contact.name or "").strip()
        parts = full_nm.split()
        g_first_name = parts[0] if parts else ""

    # Calculate balance_due if not provided
    tot = req.total_amount or Decimal("0.00")
    dep = req.deposit_amount or Decimal("0.00")
    fin = req.final_payment_amount or Decimal("0.00")
    bal = req.balance_due if req.balance_due is not None else max(Decimal("0.00"), tot - dep - fin)

    booking = Booking(
        id=uuid.uuid4(),
        reservation_reference=req.reservation_reference,
        contact_id=contact.id,
        conversation_id=uuid.UUID(conv_id) if isinstance(conv_id, str) else conv_id,
        source=req.source or "direct",
        status=req.status or "pending",
        check_in=req.check_in,
        check_out=req.check_out,
        checkout_time=co_time,
        guest_count=req.guest_count or 1,
        has_pets=req.has_pets or False,
        guest_name=req.guest_name or contact.name,
        guest_first_name=g_first_name,
        language_tag=req.language_tag or "english",
        currency=req.currency or "USD",
        total_amount=tot,
        deposit_amount=dep,
        refundable_deposit=req.refundable_deposit or Decimal("0.00"),
        final_payment_amount=fin,
        balance_due=bal,
        deposit_due_date=req.deposit_due_date,
        payment_due_date=req.payment_due_date,
        pricing_snapshot=req.pricing_snapshot,
        internal_notes=req.internal_notes
    )

    now = datetime.now()
    if req.status == "confirmed":
        booking.confirmed_at = now
    elif req.status == "checked_in":
        booking.checked_in_at = now
    elif req.status == "completed":
        booking.completed_at = now
    elif req.status == "cancelled":
        booking.cancelled_at = now

    db.add(booking)
    await db.flush()

    for u_req in req.units:
        # If unit_name_snapshot is not provided, try to lookup Property name
        u_name = u_req.unit_name_snapshot
        if not u_name and u_req.property_id:
            p_res = await db.execute(select(Property).filter(Property.id == u_req.property_id))
            prop = p_res.scalar_one_or_none()
            if prop:
                u_name = prop.name

        prop_uuid = None
        if u_req.property_id:
            try:
                prop_uuid = uuid.UUID(u_req.property_id) if isinstance(u_req.property_id, str) else u_req.property_id
            except Exception:
                prop_uuid = None

        unit = BookingUnit(
            id=uuid.uuid4(),
            booking_id=booking.id,
            property_id=prop_uuid,
            unit_name_snapshot=u_name or "Unit",
            accommodation_amount=u_req.accommodation_amount or Decimal("0.00"),
            cleaning_fee=u_req.cleaning_fee or Decimal("0.00"),
            pet_fee=u_req.pet_fee or Decimal("0.00"),
            discount_amount=u_req.discount_amount or Decimal("0.00"),
            unit_total=u_req.unit_total or Decimal("0.00"),
            pricing_snapshot=u_req.pricing_snapshot
        )
        db.add(unit)

    await db.commit()

    stmt2 = (
        select(Booking)
        .options(
            selectinload(Booking.contact),
            selectinload(Booking.booking_units).selectinload(BookingUnit.property)
        )
        .filter(Booking.id == booking.id)
    )
    res2 = await db.execute(stmt2)
    created_booking = res2.scalar_one()

    return _format_booking(created_booking)


@router.put("/admin/bookings/{booking_id}")
async def update_booking(booking_id: str, req: BookingUpdate, db: AsyncSession = Depends(get_db)):
    stmt = (
        select(Booking)
        .options(
            selectinload(Booking.contact),
            selectinload(Booking.booking_units)
        )
        .filter(Booking.id == booking_id)
    )
    res = await db.execute(stmt)
    b = res.scalar_one_or_none()
    if not b:
        raise HTTPException(status_code=404, detail="Booking not found")

    if req.reservation_reference is not None:
        b.reservation_reference = req.reservation_reference
    if req.source is not None:
        b.source = req.source
    if req.check_in is not None:
        b.check_in = req.check_in
    if req.check_out is not None:
        b.check_out = req.check_out
    if req.checkout_time is not None:
        try:
            parts = [int(p) for p in req.checkout_time.split(":")]
            b.checkout_time = time(parts[0], parts[1], parts[2] if len(parts) > 2 else 0)
        except Exception:
            pass
    if req.guest_count is not None:
        b.guest_count = req.guest_count
    if req.has_pets is not None:
        b.has_pets = req.has_pets
    if req.guest_name is not None:
        b.guest_name = req.guest_name
        if b.contact:
            b.contact.name = req.guest_name
        if req.guest_first_name is None and req.guest_name:
            parts = req.guest_name.strip().split()
            b.guest_first_name = parts[0] if parts else ""
    if req.guest_first_name is not None:
        b.guest_first_name = req.guest_first_name
    if req.language_tag is not None:
        b.language_tag = req.language_tag
    if req.currency is not None:
        b.currency = req.currency
    if req.total_amount is not None:
        b.total_amount = req.total_amount
    if req.deposit_amount is not None:
        b.deposit_amount = req.deposit_amount
    if req.refundable_deposit is not None:
        b.refundable_deposit = req.refundable_deposit
    if req.final_payment_amount is not None:
        b.final_payment_amount = req.final_payment_amount
    if req.balance_due is not None:
        b.balance_due = req.balance_due
    elif req.total_amount is not None or req.deposit_amount is not None or req.final_payment_amount is not None:
        b.balance_due = max(Decimal("0.00"), (b.total_amount or Decimal("0.00")) - (b.deposit_amount or Decimal("0.00")) - (b.final_payment_amount or Decimal("0.00")))
    if req.deposit_due_date is not None:
        b.deposit_due_date = req.deposit_due_date
    if req.payment_due_date is not None:
        b.payment_due_date = req.payment_due_date
    if req.internal_notes is not None:
        b.internal_notes = req.internal_notes

    if req.units is not None:
        # Replace units
        for u in b.booking_units:
            await db.delete(u)
        await db.flush()

        for u_req in req.units:
            unit = BookingUnit(
                id=uuid.uuid4(),
                booking_id=b.id,
                property_id=u_req.property_id,
                unit_name_snapshot=u_req.unit_name_snapshot,
                accommodation_amount=u_req.accommodation_amount,
                cleaning_fee=u_req.cleaning_fee,
                pet_fee=u_req.pet_fee,
                discount_amount=u_req.discount_amount,
                unit_total=u_req.unit_total,
                pricing_snapshot=u_req.pricing_snapshot
            )
            db.add(unit)

    await db.commit()
    return {"status": "success"}


@router.post("/admin/bookings/{booking_id}/status")
async def update_booking_status(booking_id: str, req: StatusTransitionRequest, db: AsyncSession = Depends(get_db)):
    valid_statuses = ["pending", "confirmed", "active", "checked_in", "completed", "cancelled", "no_show"]
    if req.status not in valid_statuses:
        raise HTTPException(status_code=400, detail=f"Invalid status. Must be one of {valid_statuses}")

    stmt = select(Booking).filter(Booking.id == booking_id)
    res = await db.execute(stmt)
    b = res.scalar_one_or_none()
    if not b:
        raise HTTPException(status_code=404, detail="Booking not found")

    b.status = req.status
    now = datetime.now()
    if req.status == "confirmed" and not b.confirmed_at:
        b.confirmed_at = now
    elif req.status in ["checked_in", "active"] and not b.checked_in_at:
        b.checked_in_at = now
    elif req.status == "completed" and not b.completed_at:
        b.completed_at = now
    elif req.status == "cancelled" and not b.cancelled_at:
        b.cancelled_at = now

    await db.commit()
    return {"status": "success", "new_status": b.status}


@router.delete("/admin/bookings/{booking_id}")
async def delete_booking(booking_id: str, db: AsyncSession = Depends(get_db)):
    stmt = (
        select(Booking)
        .options(selectinload(Booking.booking_units))
        .filter(Booking.id == booking_id)
    )
    result = await db.execute(stmt)
    b = result.scalar_one_or_none()
    if not b:
        raise HTTPException(status_code=404, detail="Booking not found")

    # Delete booking units first (cascade should handle this, but be explicit)
    for unit in b.booking_units:
        await db.delete(unit)
    await db.delete(b)
    await db.commit()
    return {"status": "success", "message": "Booking deleted"}


@router.post("/admin/bookings/confirm-deposit")
async def confirm_deposit(req: ConfirmDepositRequest, db: AsyncSession = Depends(get_db)):
    if not req.booking_id and not req.phone:
        raise HTTPException(status_code=400, detail="Provide either booking_id or phone")

    stmt = (
        select(Booking)
        .options(
            selectinload(Booking.contact),
            selectinload(Booking.booking_units).selectinload(BookingUnit.property)
        )
    )
    if req.booking_id:
        stmt = stmt.filter(Booking.id == req.booking_id)
    else:
        raw_phone = (req.phone or "").strip()
        clean_phone = raw_phone.lstrip("+")
        phone_variants = list({raw_phone, clean_phone, f"+{clean_phone}"} - {""})
        stmt = (
            stmt.join(Contact)
            .filter(Contact.phone.in_(phone_variants))
            .order_by(text("CASE WHEN status = 'pending' THEN 0 ELSE 1 END"), Booking.created_at.desc())
        )

    res = await db.execute(stmt)
    b = res.scalars().first()
    if not b:
        raise HTTPException(
            status_code=404, 
            detail=f"No booking found for guest '{req.phone}'. Please create a booking for this guest first before confirming deposit."
        )

    curr_input = (req.currency or "USD").upper().strip()
    deposit_in_usd = req.deposit_amount
    rate_used = None

    if curr_input in ("CRC", "COLONES", "₡"):
        # Look up exchange rate from pricing_settings
        rate_res = await db.execute(select(PricingSetting).filter(PricingSetting.key == "usd_to_crc_exchange_rate"))
        rate_setting = rate_res.scalar_one_or_none()
        try:
            exchange_rate = Decimal(str(rate_setting.value)) if rate_setting and rate_setting.value else Decimal("452.94")
        except Exception:
            exchange_rate = Decimal("452.94")

        if exchange_rate <= Decimal("0"):
            exchange_rate = Decimal("452.94")

        rate_used = exchange_rate
        deposit_in_usd = round(req.deposit_amount / exchange_rate, 2)

    total = b.total_amount or Decimal("0.00")
    if deposit_in_usd > total:
        raise HTTPException(
            status_code=400, 
            detail=f"deposit_amount ({req.deposit_amount} {curr_input} = ${deposit_in_usd:.2f} USD) cannot exceed total_amount (${total:.2f} USD)"
        )

    b.deposit_amount = deposit_in_usd
    b.balance_due = total - deposit_in_usd
    if req.payment_due_date is not None:
        b.payment_due_date = req.payment_due_date

    # Add conversion note to internal_notes if paid in Colones
    if rate_used:
        curr_notes = b.internal_notes or ""
        conv_note = f"[Deposit received: ₡{int(req.deposit_amount):,} CRC = ${deposit_in_usd:.2f} USD @ rate 1 USD = {rate_used} CRC]"
        if conv_note not in curr_notes:
            b.internal_notes = f"{curr_notes}\n{conv_note}".strip()

    now = datetime.now()
    if b.status == "pending":
        b.status = "confirmed"
        if not b.confirmed_at:
            b.confirmed_at = now

    # Sync guest names between Contact and Booking
    contact = b.contact
    booking_name = (b.guest_name or "").strip()
    contact_name = (contact.name or "").strip()

    if not contact_name or contact_name.lower() in ("unknown", "unknown guest"):
        # Contact has no meaningful name — pull from booking
        if booking_name:
            contact.name = booking_name
    elif contact_name:
        # Contact already has a name — push to booking
        b.guest_name = contact_name
        if not b.guest_first_name:
            b.guest_first_name = contact_name.split()[0]

    await db.commit()

    stmt2 = (
        select(Booking)
        .options(
            selectinload(Booking.contact),
            selectinload(Booking.booking_units).selectinload(BookingUnit.property)
        )
        .filter(Booking.id == b.id)
    )
    res2 = await db.execute(stmt2)
    updated = res2.scalar_one()
    return _format_booking(updated)


class ConfirmFinalPaymentRequest(BaseModel):
    phone: Optional[str] = None
    booking_id: Optional[str] = None
    payment_amount: Decimal = Field(..., ge=0)
    currency: Optional[str] = "USD"

@router.post("/admin/bookings/confirm-final-payment")
async def confirm_final_payment(req: ConfirmFinalPaymentRequest, db: AsyncSession = Depends(get_db)):
    if not req.booking_id and not req.phone:
        raise HTTPException(status_code=400, detail="Provide either booking_id or phone")

    stmt = (
        select(Booking)
        .options(
            selectinload(Booking.contact),
            selectinload(Booking.booking_units).selectinload(BookingUnit.property)
        )
    )
    if req.booking_id:
        stmt = stmt.filter(Booking.id == req.booking_id)
    else:
        raw_phone = (req.phone or "").strip()
        clean_phone = raw_phone.lstrip("+")
        phone_variants = list({raw_phone, clean_phone, f"+{clean_phone}"} - {""})
        stmt = (
            stmt.join(Contact)
            .filter(Contact.phone.in_(phone_variants))
            .order_by(text("CASE WHEN status = 'pending' THEN 0 ELSE 1 END"), Booking.created_at.desc())
        )

    res = await db.execute(stmt)
    b = res.scalars().first()
    if not b:
        raise HTTPException(
            status_code=404,
            detail=f"No booking found for guest '{req.phone}'. Please create a booking first."
        )

    curr_input = (req.currency or "USD").upper().strip()
    payment_in_usd = req.payment_amount
    rate_used = None

    if curr_input in ("CRC", "COLONES", "₡"):
        rate_res = await db.execute(select(PricingSetting).filter(PricingSetting.key == "usd_to_crc_exchange_rate"))
        rate_setting = rate_res.scalar_one_or_none()
        try:
            exchange_rate = Decimal(str(rate_setting.value)) if rate_setting and rate_setting.value else Decimal("452.94")
        except Exception:
            exchange_rate = Decimal("452.94")

        if exchange_rate <= Decimal("0"):
            exchange_rate = Decimal("452.94")

        rate_used = exchange_rate
        payment_in_usd = round(req.payment_amount / exchange_rate, 2)

    current_balance = b.balance_due or Decimal("0.00")
    if payment_in_usd > current_balance:
        raise HTTPException(
            status_code=400,
            detail=f"Payment ({req.payment_amount} {curr_input} = ${payment_in_usd:.2f} USD) exceeds balance due (${current_balance:.2f} USD)"
        )

    b.final_payment_amount = (b.final_payment_amount or Decimal("0.00")) + payment_in_usd
    b.balance_due = max(Decimal("0.00"), current_balance - payment_in_usd)

    # Add conversion note to internal_notes if paid in Colones
    if rate_used:
        curr_notes = b.internal_notes or ""
        conv_note = f"[Final payment received: ₡{int(req.payment_amount):,} CRC = ${payment_in_usd:.2f} USD @ rate 1 USD = {rate_used} CRC]"
        if conv_note not in curr_notes:
            b.internal_notes = f"{curr_notes}\n{conv_note}".strip()
    else:
        curr_notes = b.internal_notes or ""
        conv_note = f"[Final payment received: ${payment_in_usd:.2f} USD]"
        if conv_note not in curr_notes:
            b.internal_notes = f"{curr_notes}\n{conv_note}".strip()

    await db.commit()

    stmt2 = (
        select(Booking)
        .options(
            selectinload(Booking.contact),
            selectinload(Booking.booking_units).selectinload(BookingUnit.property)
        )
        .filter(Booking.id == b.id)
    )
    res2 = await db.execute(stmt2)
    updated = res2.scalar_one()
    return _format_booking(updated)
