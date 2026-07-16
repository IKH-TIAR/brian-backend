from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
import uuid

from database import get_db
from models import BungalowCode

router = APIRouter()


# --- Pydantic schemas ---

class BungalowCodeCreate(BaseModel):
    bungalow: str
    door_code: str | None = None
    lockbox_code: str | None = None
    lockbox_location: str | None = None
    wifi_name: str | None = None
    wifi_password: str | None = None
    special_notes: str | None = None


class BungalowCodeUpdate(BaseModel):
    bungalow: str | None = None
    door_code: str | None = None
    lockbox_code: str | None = None
    lockbox_location: str | None = None
    wifi_name: str | None = None
    wifi_password: str | None = None
    special_notes: str | None = None


# --- Routes ---

@router.get("/admin/bungalows")
async def list_bungalows(db: AsyncSession = Depends(get_db)):
    stmt = select(BungalowCode).order_by(BungalowCode.bungalow)
    result = await db.execute(stmt)
    codes = result.scalars().all()

    return [
        {
            "id": str(c.id),
            "bungalow": c.bungalow,
            "door_code": c.door_code or "",
            "lockbox_code": c.lockbox_code or "",
            "lockbox_location": c.lockbox_location or "",
            "wifi_name": c.wifi_name or "",
            "wifi_password": c.wifi_password or "",
            "special_notes": c.special_notes or "",
        }
        for c in codes
    ]


@router.post("/admin/bungalows")
async def create_bungalow(req: BungalowCodeCreate, db: AsyncSession = Depends(get_db)):
    # Check for duplicate bungalow name
    existing = await db.execute(
        select(BungalowCode).filter(BungalowCode.bungalow == req.bungalow)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="A bungalow with this name already exists")

    code = BungalowCode(
        id=uuid.uuid4(),
        bungalow=req.bungalow,
        door_code=req.door_code,
        lockbox_code=req.lockbox_code,
        lockbox_location=req.lockbox_location,
        wifi_name=req.wifi_name,
        wifi_password=req.wifi_password,
        special_notes=req.special_notes,
    )
    db.add(code)
    await db.commit()
    await db.refresh(code)

    return {
        "id": str(code.id),
        "bungalow": code.bungalow,
        "door_code": code.door_code or "",
        "lockbox_code": code.lockbox_code or "",
        "lockbox_location": code.lockbox_location or "",
        "wifi_name": code.wifi_name or "",
        "wifi_password": code.wifi_password or "",
        "special_notes": code.special_notes or "",
    }


@router.put("/admin/bungalows/{bungalow_id}")
async def update_bungalow(bungalow_id: str, req: BungalowCodeUpdate, db: AsyncSession = Depends(get_db)):
    stmt = select(BungalowCode).filter(BungalowCode.id == bungalow_id)
    result = await db.execute(stmt)
    code = result.scalar_one_or_none()

    if not code:
        raise HTTPException(status_code=404, detail="Bungalow not found")

    if req.bungalow is not None:
        code.bungalow = req.bungalow
    if req.door_code is not None:
        code.door_code = req.door_code
    if req.lockbox_code is not None:
        code.lockbox_code = req.lockbox_code
    if req.lockbox_location is not None:
        code.lockbox_location = req.lockbox_location
    if req.wifi_name is not None:
        code.wifi_name = req.wifi_name
    if req.wifi_password is not None:
        code.wifi_password = req.wifi_password
    if req.special_notes is not None:
        code.special_notes = req.special_notes

    await db.commit()

    return {"status": "success"}


@router.delete("/admin/bungalows/{bungalow_id}")
async def delete_bungalow(bungalow_id: str, db: AsyncSession = Depends(get_db)):
    stmt = select(BungalowCode).filter(BungalowCode.id == bungalow_id)
    result = await db.execute(stmt)
    code = result.scalar_one_or_none()

    if not code:
        raise HTTPException(status_code=404, detail="Bungalow not found")

    await db.delete(code)
    await db.commit()

    return {"status": "success"}
