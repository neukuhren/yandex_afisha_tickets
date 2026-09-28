#!/usr/bin/env python3
"""Переключить отслеживаемое событие на другой канал продаж (виджет сеанса)."""
from __future__ import annotations

import argparse
import asyncio
import sys

from src.config import get_settings
from src.db.repository import Database
from src.parser.afisha_client import AfishaClient


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("event_id", type=int)
    parser.add_argument("session_widget_url", type=str)
    args = parser.parse_args()

    settings = get_settings()
    db = Database(settings.database_url)
    client = AfishaClient()
    try:
        resolved = await client.resolve_event_input(args.session_widget_url)
        if not resolved.direct_session:
            print("URL должен быть ссылкой на /w/sessions/...", file=sys.stderr)
            return 1
        session = resolved.direct_session
        await db.update_event_sales_channel(
            args.event_id,
            source_url=args.session_widget_url,
            widget_event_id=resolved.widget_event_id,
            region_id=resolved.region_id,
            client_key=resolved.client_key or "",
            session_key=session.key,
            session_id=session.session_id,
        )
        print(
            f"Событие {args.event_id} переключено: event_id={resolved.widget_event_id}, "
            f"session_id={session.session_id}, seats={session.available_seat_count}"
        )
        return 0
    finally:
        await client.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
