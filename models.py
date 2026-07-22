import uuid
from sqlalchemy import Column, String, Boolean, DateTime, Date, ForeignKey, Text, JSON
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from database import Base

class Contact(Base):
    __tablename__ = "contacts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    phone = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=True)
    is_returning = Column(Boolean, default=False)
    mode = Column(String, default="BOT")
    mode_reason = Column(String, nullable=True)
    mode_updated_at = Column(DateTime(timezone=False), nullable=True)
    created_at = Column(DateTime(timezone=False), server_default=func.now())

    conversations = relationship("Conversation", back_populates="contact")


class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    contact_id = Column(UUID(as_uuid=True), ForeignKey("contacts.id"), unique=True)
    bungalow = Column(String, nullable=True)
    check_in = Column(Date, nullable=True)
    check_out = Column(Date, nullable=True)
    created_at = Column(DateTime(timezone=False), server_default=func.now())
    last_message_at = Column(DateTime(timezone=False), server_default=func.now())
    payment_due_date = Column(Date, nullable=True)

    contact = relationship("Contact", back_populates="conversations")
    messages = relationship("Message", back_populates="conversation", order_by="Message.created_at")


class Message(Base):
    __tablename__ = "messages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id = Column(UUID(as_uuid=True), ForeignKey("conversations.id"))
    role = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    status = Column(String, default="pending")
    is_read = Column(Boolean, default=False)
    escalated = Column(Boolean, default=False)
    escalation_reason = Column(String, nullable=True)
    execution_id = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=False), server_default=func.now())

    conversation = relationship("Conversation", back_populates="messages")


class AdminCommand(Base):
    __tablename__ = "admin_commands"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    command = Column(String(50), unique=True, nullable=False)
    label = Column(String(100), nullable=False)
    category = Column(String(30), nullable=False)
    ai_system_prompt = Column(Text, nullable=True)
    template_en = Column(Text, nullable=True)
    template_es = Column(Text, nullable=True)
    required_params = Column(JSONB, default=list)
    set_mode_after = Column(String(10), nullable=True)
    is_ai = Column(Boolean, default=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class BungalowCode(Base):
    __tablename__ = "bungalow_codes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    bungalow = Column(String, unique=True, nullable=False)
    door_code = Column(String, nullable=True)
    lockbox_code = Column(String, nullable=True)
    lockbox_location = Column(String, nullable=True) 
    wifi_name = Column(String, nullable=True)
    wifi_password = Column(String, nullable=True)
    special_notes = Column(Text, nullable=True)


class PushSubscription(Base):
    __tablename__ = "push_subscriptions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    endpoint = Column(Text, unique=True, nullable=False)
    p256dh = Column(Text, nullable=False)
    auth = Column(Text, nullable=False)
    user_agent = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

