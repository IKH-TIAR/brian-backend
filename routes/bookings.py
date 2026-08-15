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
from models import Booking, BookingUnit, Contact, Property

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
    contact_id: str
    conversation_id: Optional[str] = None
    source: Optional[str] = None
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
    balance_due: Optional[Decimal] = Field(Decimal("0.00"), ge=0)
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
    balance_due: Optional[Decimal] = Field(None, ge=0)
    deposit_due_date: Optional[date] = None
    payment_due_date: Optional[date] = None
    internal_notes: Optional[str] = None
    units: Optional[List[BookingUnitCreate]] = None

class StatusTransitionRequest(BaseModel):
    status: str

# Helper to format booking dictionary
def _format_booking(b: Booking) -> dict:
    units_out = []
    if b.booking_units:
        for u in b.booking_units:
            units_out.append({
                "id": str(u.id),
                "property_id": str(u.property_id) if u.property_id else None,
                "property_name": u.property.name if u.property else None,
                "unit_name_snapshot": u.unit_name_snapshot or (u.property.name if u.property else ""),
                "accommodation_amount": float(u.accommodation_amount or 0),
                "cleaning_fee": float(u.cleaning_fee or 0),
                "pet_fee": float(u.pet_fee or 0),
                "discount_amount": float(u.discount_amount or 0),
                "unit_total": float(u.unit_total or 0),
                "pricing_snapshot": u.pricing_snapshot
            })

    contact_data = None
    if b.contact:
        contact_data = {
            "id": str(b.contact.id),
            "phone": b.contact.phone,
            "name": b.contact.name,
            "is_returning": b.contact.is_returning,
            "mode": b.contact.mode
        }

    return {
        "id": str(b.id),
        "reservation_reference": b.reservation_reference or "",
        "contact_id": str(b.contact_id),
        "conversation_id": str(b.conversation_id) if b.conversation_id else None,
        "contact": contact_data,
        "source": b.source or "",
        "status": b.status,
        "check_in": b.check_in.isoformat() if b.check_in else None,
        "check_out": b.check_out.isoformat() if b.check_out else None,
        "checkout_time": str(b.checkout_time) if b.checkout_time else "11:00:00",
        "guest_count": b.guest_count or 1,
        "has_pets": b.has_pets,
        "guest_name": b.guest_name or "",
        "guest_first_name": b.guest_first_name or "",
        "language_tag": b.language_tag or "english",
        "currency": b.currency or "USD",
        "total_amount": float(b.total_amount or 0),
        "deposit_amount": float(b.deposit_amount or 0),
        "balance_due": float(b.balance_due or 0),
        "deposit_due_date": b.deposit_due_date.isoformat() if b.deposit_due_date else None,
        "payment_due_date": b.payment_due_date.isoformat() if b.payment_due_date else None,
        "pricing_snapshot": b.pricing_snapshot,
        "internal_notes": b.internal_notes or "",
        "confirmed_at": b.confirmed_at.isoformat() if b.confirmed_at else None,
        "checked_in_at": b.checked_in_at.isoformat() if b.checked_in_at else None,
        "completed_at": b.completed_at.isoformat() if b.completed_at else None,
        "cancelled_at": b.cancelled_at.isoformat() if b.cancelled_at else None,
        "created_at": b.created_at.isoformat() if b.created_at else None,
        "units": units_out
    }

# --- Routes ---

@router.get("/admin/bookings")
async def list_bookings(
    status_filter: Optional[str] = Query(None, alias="status"),
    check_in_from: Optional[date] = Query(None),
    check_in_to: Optional[date] = Query(None),
    search: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db)
):
    stmt = (
        select(Booking)
        .options(
            selectinload(Booking.contact),
            selectinload(Booking.booking_units).selectinload(BookingUnit.property)
        )
    )

    if status_filter:
        stmt = stmt.filter(Booking.status == status_filter)
    if check_in_from:
        stmt = stmt.filter(Booking.check_in >= check_in_from)
    if check_in_to:
        stmt = stmt.filter(Booking.check_in <= check_in_to)
    if search:
        stmt = stmt.join(Contact).filter(
            or_(
                Booking.reservation_reference.ilike(f"%{search}%"),
                Booking.guest_name.ilike(f"%{search}%"),
                Contact.phone.ilike(f"%{search}%"),
                Contact.name.ilike(f"%{search}%")
            )
        )

    stmt = stmt.order_by(Booking.check_in.desc().nullslast(), Booking.created_at.desc())
    res = await db.execute(stmt)
    bookings = res.scalars().all()

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
    contact_res = await db.execute(select(Contact).filter(Contact.id == req.contact_id))
    contact = contact_res.scalar_one_or_none()
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")

    # Parse checkout_time string to time object if needed
    co_time = None
    if req.checkout_time:
        try:
            parts = [int(p) for p in req.checkout_time.split(":")]
            co_time = time(parts[0], parts[1], parts[2] if len(parts) > 2 else 0)
        except Exception:
            co_time = time(11, 0, 0)

    booking = Booking(
        id=uuid.uuid4(),
        reservation_reference=req.reservation_reference,
        contact_id=req.contact_id,
        conversation_id=req.conversation_id,
        source=req.source,
        status=req.status,
        check_in=req.check_in,
        check_out=req.check_out,
        checkout_time=co_time,
        guest_count=req.guest_count,
        has_pets=req.has_pets,
        guest_name=req.guest_name or contact.name,
        guest_first_name=req.guest_first_name,
        language_tag=req.language_tag,
        currency=req.currency,
        total_amount=req.total_amount,
        deposit_amount=req.deposit_amount,
        balance_due=req.balance_due,
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
        unit = BookingUnit(
            id=uuid.uuid4(),
            booking_id=booking.id,
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

    # Re-fetch for return
    stmt = (
        select(Booking)
        .options(
            selectinload(Booking.contact),
            selectinload(Booking.booking_units).selectinload(BookingUnit.property)
        )
        .filter(Booking.id == booking.id)
    )
    res = await db.execute(stmt)
    created_booking = res.scalar_one()

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
    if req.balance_due is not None:
        b.balance_due = req.balance_due
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
