import asyncio
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from dotenv import load_dotenv
import httpx

from database import get_db, Base, engine
from models import Message, Contact, Conversation
from routes import conversations, commands, bungalows

load_dotenv()

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
                            await manager.broadcast({
                                "type": "new_message",
                                "data": {
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
                            })
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

app.include_router(conversations.router, prefix="/api")
app.include_router(commands.router, prefix="/api")
app.include_router(bungalows.router, prefix="/api")

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
