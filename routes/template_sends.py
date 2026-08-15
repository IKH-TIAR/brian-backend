from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models import WhatsappTemplateSend

router = APIRouter()

PAGE_SIZE = 50

@router.get("/admin/template-sends")
async def list_template_sends(
    template_key: Optional[str] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    limit: int = Query(PAGE_SIZE, ge=1, le=200),
    db: AsyncSession = Depends(get_db)
):
    stmt = select(WhatsappTemplateSend)

    if template_key:
        stmt = stmt.filter(WhatsappTemplateSend.template_key == template_key)
    if status_filter:
        stmt = stmt.filter(WhatsappTemplateSend.status == status_filter)

    stmt = stmt.order_by(desc(WhatsappTemplateSend.sent_at), desc(WhatsappTemplateSend.id))

    offset = (page - 1) * limit
    stmt = stmt.offset(offset).limit(limit + 1)

    res = await db.execute(stmt)
    rows = res.scalars().all()

    has_more = len(rows) > limit
    rows = rows[:limit]

    out = []
    for r in rows:
        out.append({
            "id": str(r.id),
            "template_key": r.template_key,
            "template_name": r.template_name or "",
            "phone": r.phone,
            "language": r.language_tag or "english",
            "status": r.status,
            "timestamp": r.sent_at.isoformat() if r.sent_at else None
        })

    return {"sends": out, "page": page, "has_more": has_more}
