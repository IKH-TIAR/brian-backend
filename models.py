import uuid
from sqlalchemy import Column, String, Boolean, DateTime, Date, ForeignKey, Text, JSON, Numeric, Integer, UniqueConstraint, Index, text
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
    __table_args__ = (
        Index("ix_conversations_last_msg", text("last_message_at DESC NULLS LAST")),
    )

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
    __table_args__ = (
        Index("ix_messages_conv_created", "conversation_id", text("created_at DESC")),
        Index("ix_messages_created_at", text("created_at DESC")),
        Index("ix_messages_unread", "conversation_id", postgresql_where=text("role = 'user' AND is_read = FALSE")),
    )

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
    __table_args__ = (
        Index("ix_admin_commands_cat_label", "category", "label"),
    )

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


# ==================================================
# PRICING MANAGEMENT MODELS
# ==================================================

class Property(Base):
    __tablename__ = "properties"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code = Column(String(50), unique=True, nullable=False)
    name = Column(String(150), nullable=False)
    standard_capacity = Column(Integer, nullable=False, default=1)
    maximum_capacity = Column(Integer, nullable=False, default=1)
    pets_allowed = Column(Boolean, default=True)
    active = Column(Boolean, default=True)
    display_order = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    rate_plans = relationship("PropertyRatePlan", back_populates="property", cascade="all, delete-orphan")


class PropertyRatePlan(Base):
    __tablename__ = "property_rate_plans"
    __table_args__ = (
        Index("ix_rate_plans_property", "property_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    property_id = Column(UUID(as_uuid=True), ForeignKey("properties.id"), nullable=False)
    code = Column(String(50), nullable=False)
    name = Column(String(150), nullable=False)
    standard_capacity = Column(Integer, nullable=False, default=1)
    maximum_capacity = Column(Integer, nullable=False, default=1)
    cleaning_fee = Column(Numeric(10, 2), nullable=False, default=0.00)
    extra_person_fee_per_night = Column(Numeric(10, 2), nullable=False, default=10.00)
    refundable_deposit = Column(Numeric(10, 2), nullable=False, default=0.00)
    pets_allowed = Column(Boolean, default=True)
    active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    property = relationship("Property", back_populates="rate_plans")
    seasonal_prices = relationship("PropertySeasonPrice", back_populates="rate_plan", cascade="all, delete-orphan")
    promotional_prices = relationship("PromotionPropertyPrice", back_populates="rate_plan", cascade="all, delete-orphan")


class PricingTier(Base):
    __tablename__ = "pricing_tiers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code = Column(String(50), unique=True, nullable=False)
    name = Column(String(100), nullable=False)
    active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    seasonal_prices = relationship("PropertySeasonPrice", back_populates="pricing_tier")


class Season(Base):
    __tablename__ = "seasons"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code = Column(String(50), unique=True, nullable=False)
    name = Column(String(100), nullable=False)
    priority = Column(Integer, nullable=False, default=100)
    active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    periods = relationship("SeasonPeriod", back_populates="season", cascade="all, delete-orphan")
    seasonal_prices = relationship("PropertySeasonPrice", back_populates="season", cascade="all, delete-orphan")


class SeasonPeriod(Base):
    __tablename__ = "season_periods"
    __table_args__ = (
        Index("ix_season_periods_season", "season_id", "start_date", "end_date"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    season_id = Column(UUID(as_uuid=True), ForeignKey("seasons.id"), nullable=False)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    active = Column(Boolean, default=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    season = relationship("Season", back_populates="periods")


class PropertySeasonPrice(Base):
    __tablename__ = "property_season_prices"
    __table_args__ = (
        UniqueConstraint("property_rate_plan_id", "season_id", "pricing_tier_id", name="uq_rateplan_season_tier"),
        Index("ix_psp_season_tier", "season_id", "pricing_tier_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    property_rate_plan_id = Column(UUID(as_uuid=True), ForeignKey("property_rate_plans.id"), nullable=False)
    season_id = Column(UUID(as_uuid=True), ForeignKey("seasons.id"), nullable=False)
    pricing_tier_id = Column(UUID(as_uuid=True), ForeignKey("pricing_tiers.id"), nullable=False)
    nightly_rate = Column(Numeric(10, 2), nullable=False, default=0.00)
    active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    rate_plan = relationship("PropertyRatePlan", back_populates="seasonal_prices")
    season = relationship("Season", back_populates="seasonal_prices")
    pricing_tier = relationship("PricingTier", back_populates="seasonal_prices")


class Promotion(Base):
    __tablename__ = "promotions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(150), nullable=False)
    description = Column(Text, nullable=True)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    enabled = Column(Boolean, default=True)
    waive_pet_fee = Column(Boolean, default=False)
    priority = Column(Integer, default=1000)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    property_prices = relationship("PromotionPropertyPrice", back_populates="promotion", cascade="all, delete-orphan")


class PromotionPropertyPrice(Base):
    __tablename__ = "promotion_property_prices"
    __table_args__ = (
        UniqueConstraint("promotion_id", "property_rate_plan_id", name="uq_promotion_rateplan"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    promotion_id = Column(UUID(as_uuid=True), ForeignKey("promotions.id"), nullable=False)
    property_rate_plan_id = Column(UUID(as_uuid=True), ForeignKey("property_rate_plans.id"), nullable=False)
    nightly_rate = Column(Numeric(10, 2), nullable=False, default=0.00)
    active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    promotion = relationship("Promotion", back_populates="property_prices")
    rate_plan = relationship("PropertyRatePlan", back_populates="promotional_prices")


class PricingSetting(Base):
    __tablename__ = "pricing_settings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    key = Column(String(50), unique=True, nullable=False)
    value = Column(String(255), nullable=False)
    description = Column(String(255), nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


