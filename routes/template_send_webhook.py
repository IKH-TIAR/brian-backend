from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
import httpx
import os
from datetime import date

from database import get_db
from models import Booking, BookingUnit, Contact, Property, PropertyTemplateConfig, BungalowCode

router = APIRouter()


class SendTemplateRequest(BaseModel):
    template_key: str  # pre_arrival, pre_checkout, post_checkout_thankyou
    booking_unit_id: str | None = None  # Required for pre_arrival if multiple units


def _format_time_12h(t) -> str:
    """Format a time object to '11:00 AM' style string."""
    if not t:
        return ""
    hour = t.hour
    minute = t.minute
    ampm = "AM" if hour < 12 else "PM"
    display_hour = hour if hour <= 12 else hour - 12
    if display_hour == 0:
        display_hour = 12
    return f"{display_hour}:{minute:02d} {ampm}"


async def dispatch_booking_template(
    db: AsyncSession,
    booking_id: str,
    template_key: str,
    booking_unit_id: str | None = None
) -> dict:
    """
    Assemble all 20 fields required by the n8n Template Dispatcher sub-workflow
    and POST them to the n8n webhook for sending.
    Supports: pre_arrival, pre_checkout, post_checkout_thankyou
    """
    webhook_url = os.getenv("N8N_TEMPLATE_WEBHOOK_URL")
    if not webhook_url:
        raise HTTPException(status_code=500, detail="N8N_TEMPLATE_WEBHOOK_URL not configured")

    # Validate template_key
    allowed_keys = {"pre_arrival", "pre_checkout", "post_checkout_thankyou"}
    if template_key not in allowed_keys:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid template_key. Must be one of: {', '.join(sorted(allowed_keys))}"
        )

    # Load booking with contact
    stmt = (
        select(Booking)
        .options(selectinload(Booking.contact))
        .filter(Booking.id == booking_id)
    )
    result = await db.execute(stmt)
    booking = result.scalar_one_or_none()

    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")

    if not booking.contact:
        raise HTTPException(status_code=404, detail="No contact linked to this booking")

    phone = (booking.contact.phone or "").lstrip("+")
    if not phone:
        raise HTTPException(status_code=400, detail="Contact has no phone number")

    language_tag = booking.language_tag or "english"
    today = date.today().isoformat()

    # Base payload — all 20 fields with defaults
    payload = {
        "template_key": template_key,
        "phone": phone,
        "language_tag": language_tag,
        "booking_id": str(booking.id),
        "booking_ref": str(booking.id),
        "unit_name": "",
        "door_entry": "",
        "lockbox_backup": "",
        "lockbox_location": "",
        "wifi_name": "",
        "wifi_password": "",
        "map_link": "",
        "checkout_time": "",
        "checkin_time": "",
        "balance_due": "",
        "first_name": "",
        "guest_identifier": "",
        "reason_text": "",
        "booking_unit_id": "",
        "dedupe_key": "",
    }

    # ── PRE-ARRIVAL ─────────────────────────────────────────────
    if template_key == "pre_arrival":
        # Join: booking_units → property → property_template_config → bungalow_codes
        bu_stmt = (
            select(BookingUnit)
            .options(
                selectinload(BookingUnit.property)
                .selectinload(Property.template_configs)
                .selectinload(PropertyTemplateConfig.bungalow_code)
            )
            .filter(BookingUnit.booking_id == booking.id)
        )
        # If a specific booking_unit_id was provided, narrow down
        if booking_unit_id:
            bu_stmt = bu_stmt.filter(BookingUnit.id == booking_unit_id)

        bu_result = await db.execute(bu_stmt)
        units = bu_result.scalars().all()

        if not units:
            raise HTTPException(
                status_code=404,
                detail="No booking units found for this booking. Cannot send pre-arrival template."
            )

        bu = units[0]
        payload["booking_unit_id"] = str(bu.id)

        prop = bu.property
        if not prop:
            raise HTTPException(status_code=404, detail="No property linked to booking unit.")

        payload["unit_name"] = prop.name or ""

        # Get the active template config for this property
        tc = next((c for c in (prop.template_configs or []) if c.is_active), None)
        if tc:
            payload["map_link"] = tc.map_link or ""
            if tc.default_checkout_time:
                payload["checkout_time"] = _format_time_12h(tc.default_checkout_time)

            bc = tc.bungalow_code
            if bc:
                payload["door_entry"] = bc.door_code or ""
                payload["lockbox_backup"] = bc.lockbox_code or ""
                payload["lockbox_location"] = bc.lockbox_location or ""
                payload["wifi_name"] = bc.wifi_name or ""
                payload["wifi_password"] = bc.wifi_password or ""

        checkin_date = booking.check_in.isoformat() if booking.check_in else today
        payload["dedupe_key"] = f"pre_arrival:{booking.id}:{bu.id}:{checkin_date}"
        payload["checkin_time"] = "03:00 PM"  # Default check-in time

    # ── PRE-CHECKOUT ────────────────────────────────────────────
    elif template_key == "pre_checkout":
        checkout_time = booking.checkout_time
        payload["checkout_time"] = _format_time_12h(checkout_time) if checkout_time else "11:00 AM"

        checkout_date = booking.check_out.isoformat() if booking.check_out else today
        payload["dedupe_key"] = f"pre_checkout:{booking.id}:{checkout_date}"

    # ── POST-CHECKOUT THANK YOU ─────────────────────────────────
    elif template_key == "post_checkout_thankyou":
        payload["first_name"] = booking.guest_first_name or booking.guest_name or ""

        checkout_date = booking.check_out.isoformat() if booking.check_out else today
        payload["dedupe_key"] = f"post_checkout_thankyou:{booking.id}:{checkout_date}"

    # ── POST to n8n webhook ─────────────────────────────────────
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(webhook_url, json=payload, timeout=30.0)
            response.raise_for_status()
            try:
                return response.json()
            except Exception:
                return {"success": True, "message": response.text or "Template sent successfully"}
        except httpx.HTTPError as e:
            raise HTTPException(status_code=500, detail=f"Failed to send template via n8n: {str(e)}")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Template send error: {str(e)}")


@router.post("/admin/bookings/{booking_id}/send-template")
async def send_template(booking_id: str, req: SendTemplateRequest, db: AsyncSession = Depends(get_db)):
    """
    HTTP endpoint to trigger template dispatch for a specific booking.
    """
    return await dispatch_booking_template(
        db=db,
        booking_id=booking_id,
        template_key=req.template_key,
        booking_unit_id=req.booking_unit_id
    )
