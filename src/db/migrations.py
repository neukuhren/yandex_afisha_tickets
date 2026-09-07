from __future__ import annotations

from sqlalchemy import text

from src.db.models import Base


async def run_migrations(engine) -> None:
    statements = [
        "ALTER TABLE tracked_events ADD COLUMN IF NOT EXISTS status VARCHAR(32) NOT NULL DEFAULT 'active'",
        "ALTER TABLE tracked_events ADD COLUMN IF NOT EXISTS pending_reason VARCHAR(64)",
        "ALTER TABLE tracked_events ADD COLUMN IF NOT EXISTS pending_sessions JSONB",
        "UPDATE tracked_events SET status = 'active' WHERE status IS NULL OR status = ''",
        "UPDATE tracked_events SET status = 'active' WHERE session_key IS NOT NULL AND session_key != '' AND status = 'pending'",
    ]
    async with engine.begin() as conn:
        for statement in statements:
            await conn.execute(text(statement))
