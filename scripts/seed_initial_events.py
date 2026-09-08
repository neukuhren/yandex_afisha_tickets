#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import logging

from src.config import get_settings
from src.db.repository import Database
from src.parser.afisha_client import AfishaClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

INITIAL_URLS = [
    "https://widget.afisha.yandex.ru/w/events/574450?regionId=47&clientKey=85231729-d812-4fef-ab69-919e45f9cfb1",
    "https://afisha.yandex.ru/nizhny-novgorod/sport/hockey-torpedo-ak-bars",
]


async def seed_url(db: Database, parser: AfishaClient, url: str) -> None:
    existing = await db.get_event_by_url(url)
    if existing:
        logger.info("Уже есть: %s", url)
        return

    resolved = await parser.resolve_event_input(url)
    if resolved.widget_event_id > 0:
        duplicate = await db.get_event_by_widget_id(resolved.widget_event_id)
        if duplicate:
            logger.info("Уже отслеживается по widget id: %s", resolved.widget_event_id)
            return

    sessions = []
    meta = None
    if resolved.widget_event_id > 0:
        meta, sessions = await parser.discover_sessions(
            resolved.widget_event_id,
            resolved.region_id,
            resolved.client_key,
        )

    if sessions:
        session = sessions[0]
        await db.create_event(
            source_url=url,
            widget_event_id=resolved.widget_event_id,
            region_id=resolved.region_id,
            client_key=meta.client_key if meta else (resolved.client_key or ""),
            session_key=session.key,
            session_id=session.session_id,
            title=resolved.title,
            venue_name=session.venue_name,
            venue_address=session.venue_address,
            session_datetime=session.session_date,
        )
        logger.info("Добавлено: %s (%s)", resolved.title, session.session_date)
        return

    pending_reason = "no_widget" if resolved.widget_event_id <= 0 else "no_sessions"
    client_key = resolved.client_key or (meta.client_key if meta else "")
    await db.create_pending_event(
        source_url=url,
        widget_event_id=resolved.widget_event_id,
        region_id=resolved.region_id,
        client_key=client_key,
        title=resolved.title,
        pending_reason=pending_reason,
    )
    logger.info("Добавлено в ожидание: %s (%s)", resolved.title, pending_reason)


async def main() -> None:
    settings = get_settings()
    db = Database(settings)
    await db.init()
    await db.ensure_admins(settings)
    parser = AfishaClient()
    try:
        for url in INITIAL_URLS:
            try:
                await seed_url(db, parser, url)
            except Exception as exc:
                logger.warning("Не удалось добавить %s: %s", url, exc)
    finally:
        await parser.close()
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
