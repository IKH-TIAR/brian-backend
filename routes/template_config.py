import uuid
from datetime import time
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database import get_db
from models import PropertyTemplateConfig, Property, BungalowCode

router = APIRouter()

class TemplateConfigUpdate(BaseModel):
    map_link: Optional[str] = None
    default_checkout_time: Optional[str] = None
    is_active: Optional[bool] = None
    bungalow_code_id: Optional[str] = None

@router.get("/admin/template-config")
async def list_template_configs(db: AsyncSession = Depends(get_db)):
    stmt = (
        select(PropertyTemplateConfig)
        .options(
            selectinload(PropertyTemplateConfig.property),
            selectinload(PropertyTemplateConfig.bungalow_code)
        )
        .join(Property, PropertyTemplateConfig.property_id == Property.id)
        .order_by(Property.display_order, Property.code)
    )
    res = await db.execute(stmt)
    configs = res.scalars().all()

    out = []
    for c in configs:
        out.append({
            "id": str(c.id),
            "property_id": str(c.property_id),
            "property_name": c.property.name if c.property else "",
            "property_code": c.property.code if c.property else "",
            "bungalow_code_id": str(c.bungalow_code_id) if c.bungalow_code_id else None,
            "bungalow_name": c.bungalow_code.bungalow if c.bungalow_code else None,
            "map_link": c.map_link or "",
            "default_checkout_time": str(c.default_checkout_time) if c.default_checkout_time else "11:00:00",
            "is_active": c.is_active
        })
    return out

@router.put("/admin/template-config/{config_id}")
async def update_template_config(config_id: str, req: TemplateConfigUpdate, db: AsyncSession = Depends(get_db)):
    stmt = select(PropertyTemplateConfig).filter(PropertyTemplateConfig.id == config_id)
    res = await db.execute(stmt)
    cfg = res.scalar_one_or_none()
    if not cfg:
        raise HTTPException(status_code=404, detail="Template configuration not found")

    if req.map_link is not None:
        cfg.map_link = req.map_link
    if req.default_checkout_time is not None:
        try:
            parts = [int(p) for p in req.default_checkout_time.split(":")]
            cfg.default_checkout_time = time(parts[0], parts[1], parts[2] if len(parts) > 2 else 0)
        except Exception:
            pass
    if req.is_active is not None:
        cfg.is_active = req.is_active
    if req.bungalow_code_id is not None:
        cfg.bungalow_code_id = req.bungalow_code_id if req.bungalow_code_id else None

    await db.commit()
    return {"status": "success"}
