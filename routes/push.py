import os
from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from typing import Optional

from database import get_db
from models import PushSubscription

router = APIRouter()

class SubscriptionKeys(BaseModel):
    p256dh: str
    auth: str

class PushSubscribeRequest(BaseModel):
    endpoint: str
    keys: SubscriptionKeys

class PushUnsubscribeRequest(BaseModel):
    endpoint: str

@router.get("/push/vapid-public-key")
async def get_vapid_public_key():
    public_key = os.getenv("VAPID_PUBLIC_KEY")
    if not public_key:
        raise HTTPException(status_code=500, detail="VAPID_PUBLIC_KEY is not configured on the server.")
    return {"public_key": public_key}

@router.post("/push/subscribe")
async def subscribe_push(
    req: PushSubscribeRequest,
    user_agent: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db)
):
    stmt = select(PushSubscription).filter(PushSubscription.endpoint == req.endpoint)
    result = await db.execute(stmt)
    sub = result.scalar_one_or_none()

    if sub:
        sub.p256dh = req.keys.p256dh
        sub.auth = req.keys.auth
        sub.user_agent = user_agent
    else:
        sub = PushSubscription(
            endpoint=req.endpoint,
            p256dh=req.keys.p256dh,
            auth=req.keys.auth,
            user_agent=user_agent
        )
        db.add(sub)

    await db.commit()
    return {"status": "success", "message": "Push subscription saved"}

@router.post("/push/unsubscribe")
async def unsubscribe_push(
    req: PushUnsubscribeRequest,
    db: AsyncSession = Depends(get_db)
):
    stmt = select(PushSubscription).filter(PushSubscription.endpoint == req.endpoint)
    result = await db.execute(stmt)
    sub = result.scalar_one_or_none()

    if sub:
        await db.delete(sub)
        await db.commit()

    return {"status": "success", "message": "Push subscription removed"}
