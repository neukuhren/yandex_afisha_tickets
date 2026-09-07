#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import logging

from src.config import get_settings
from src.db.repository import Database
from src.parser.afisha_client import AfishaClient, AfishaParserError

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

    if "widget.afisha.yandex.ru" in url:
        parsed = parser.parse_widget_url(url)
    else:
        parsed = await parser.resolve_afisha_url(url)

    meta = await parser.get_event_meta(parsed.event_id, parsed.region_id, parsed.client_key)
    if not meta.presentation_dates:
        raise AfishaParserError("Нет дат презентации")

    sessions = await parser.list_sessions(
        meta.event_id,
        meta.region_id,
        meta.client_key,
        meta.presentation_dates[0],
        meta.presentation_dates[-1],
    )
    if not sessions:
        raise AfishaParserError("Нет сеансов")

    session = sessions[0]
    await db.create_event(
        source_url=url,
        widget_event_id=meta.event_id,
        region_id=meta.region_id,
        client_key=meta.client_key,
        session_key=session.key,
        session_id=session.session_id,
        title=meta.name,
        venue_name=session.venue_name,
        venue_address=session.venue_address,
        session_datetime=session.session_date,
    )
    logger.info("Добавлено: %s (%s)", meta.name, session.session_date)


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
