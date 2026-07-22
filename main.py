import asyncio
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from sqlalchemy import text
from dotenv import load_dotenv
import httpx
import json
from pywebpush import webpush, WebPushException

from database import get_db, async_session_maker, Base, engine
from models import Message, Contact, Conversation, PushSubscription
from routes import conversations, commands, bungalows, push

load_dotenv()

security = HTTPBearer()

def verify_admin_password(credentials: HTTPAuthorizationCredentials = Depends(security)):
    correct_password = os.getenv("ADMIN_PASSWORD")
    if not correct_password:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="ADMIN_PASSWORD is not set on the server"
        )
    if credentials.credentials != correct_password:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return credentials.credentials

class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                pass

manager = ConnectionManager()

async def dispatch_web_push(payload_data: dict):
    private_key = os.getenv("VAPID_PRIVATE_KEY")
    subject = os.getenv("VAPID_SUBJECT", "mailto:admin@hermosabeachbungalows.com")
    if not private_key:
        return

    is_escalated = bool(payload_data.get("escalated"))
    phone = payload_data.get("phone", "Guest")
    content = payload_data.get("content", "")
    reason = payload_data.get("escalation_reason", "")
    role = payload_data.get("role", "user")

    # Only send push notifications for user messages or escalations
    if not is_escalated and role != "user":
        return

    if is_escalated:
        title = f"🚨 ESCALATION ALERT: {phone}"
        body = reason or content or "Conversation escalated to HUMAN mode!"
    else:
        title = f"New Message: {phone}"
        body = content

    push_payload = json.dumps({
        "title": title,
        "body": body,
        "phone": phone,
        "escalated": is_escalated,
        "icon": "https://cdn-icons-png.flaticon.com/512/3602/3602145.png"
    })

    async with async_session_maker() as session:
        stmt = select(PushSubscription)
        res = await session.execute(stmt)
        subscriptions = res.scalars().all()

        stale_ids = []
        for sub in subscriptions:
            try:
                webpush(
                    subscription_info={
                        "endpoint": sub.endpoint,
                        "keys": {
                            "p256dh": sub.p256dh,
                            "auth": sub.auth
                        }
                    },
                    data=push_payload,
                    vapid_private_key=private_key,
                    vapid_claims={"sub": subject}
                )
            except WebPushException as ex:
                if ex.response and ex.response.status_code in (404, 410):
                    stale_ids.append(sub.id)
                else:
                    print(f"Web Push send error: {ex}")
            except Exception as e:
                print(f"Unexpected Web Push error: {e}")

        if stale_ids:
            for sid in stale_ids:
                await session.execute(
                    text("DELETE FROM push_subscriptions WHERE id = :sid"),
                    {"sid": sid}
                )
            await session.commit()

async def poll_for_messages():
    last_message_id = None
    while True:
        try:
            async for session in get_db():
                stmt = select(Message).order_by(Message.created_at.desc()).limit(1)
                result = await session.execute(stmt)
                latest_msg = result.scalar_one_or_none()
                
                if latest_msg and str(latest_msg.id) != last_message_id:
                    if last_message_id is not None:
                        conv_stmt = select(Conversation).options(selectinload(Conversation.contact)).filter_by(id=latest_msg.conversation_id)
                        conv_result = await session.execute(conv_stmt)
                        conv = conv_result.scalar_one_or_none()
                        
                        if conv and conv.contact:
                            msg_data = {
                                "id": str(latest_msg.id),
                                "conversation_id": str(latest_msg.conversation_id),
                                "phone": conv.contact.phone,
                                "role": latest_msg.role,
                                "content": latest_msg.content,
                                "created_at": latest_msg.created_at.isoformat() if latest_msg.created_at else None,
                                "escalated": latest_msg.escalated,
                                "escalation_reason": latest_msg.escalation_reason,
                                "contact_mode": conv.contact.mode
                            }
                            await manager.broadcast({
                                "type": "new_message",
                                "data": msg_data
                            })
                            asyncio.create_task(dispatch_web_push(msg_data))
                    last_message_id = str(latest_msg.id)
                break
        except Exception as e:
            print(f"Polling error: {e}")
        
        await asyncio.sleep(2)

@asynccontextmanager
async def lifespan(app: FastAPI):
    polling_task = asyncio.create_task(poll_for_messages())
    yield
    polling_task.cancel()

app = FastAPI(title="HBB Admin API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(conversations.router, prefix="/api", dependencies=[Depends(verify_admin_password)])
app.include_router(commands.router, prefix="/api", dependencies=[Depends(verify_admin_password)])
app.include_router(bungalows.router, prefix="/api", dependencies=[Depends(verify_admin_password)])
app.include_router(push.router, prefix="/api", dependencies=[Depends(verify_admin_password)])

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
