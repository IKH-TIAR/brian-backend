"""One-time index creation for existing Supabase tables.

Runs at app startup (guarded by IF NOT EXISTS, so it is a no-op after the
first successful run). Plain CREATE INDEX is used instead of CONCURRENTLY
because it is compatible with the Supabase pooler (session mode) and the
dashboard is low-traffic; each statement commits on its own so table locks
are brief.

Includes whatsapp_media, which is managed by the external n8n workflow and
therefore is not defined in models.py.
"""

from sqlalchemy import text

from database import engine

INDEX_STATEMENTS = [
    "CREATE EXTENSION IF NOT EXISTS pg_trgm",
    "CREATE INDEX IF NOT EXISTS ix_messages_conv_created ON messages (conversation_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS ix_messages_created_at ON messages (created_at DESC)",
    "CREATE INDEX IF NOT EXISTS ix_messages_unread ON messages (conversation_id) WHERE role = 'user' AND is_read = FALSE",
    "CREATE INDEX IF NOT EXISTS ix_messages_content_trgm ON messages USING gin (content gin_trgm_ops)",
    "CREATE INDEX IF NOT EXISTS ix_conversations_last_msg ON conversations (last_message_at DESC NULLS LAST)",
    "CREATE INDEX IF NOT EXISTS ix_rate_plans_property ON property_rate_plans (property_id)",
    "CREATE INDEX IF NOT EXISTS ix_season_periods_season ON season_periods (season_id, start_date, end_date)",
    "CREATE INDEX IF NOT EXISTS ix_psp_season_tier ON property_season_prices (season_id, pricing_tier_id)",
    "CREATE INDEX IF NOT EXISTS ix_admin_commands_cat_label ON admin_commands (category, label)",
    "CREATE INDEX IF NOT EXISTS ix_wa_media_phone ON whatsapp_media (phone, created_at)",
]


async def ensure_indexes():
    async with engine.connect() as conn:
        for stmt in INDEX_STATEMENTS:
            try:
                await conn.exec_driver_sql(stmt)
                await conn.commit()
            except Exception as e:
                await conn.rollback()
                print(f"ensure_indexes skipped ({stmt[:70]}...): {e}")
