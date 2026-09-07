from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    bot_token: str
    database_url: str
    super_admin_id: int
    admin_ids: tuple[int, ...]
    parse_interval_seconds: int
    appearance_interval_seconds: int
    appearance_duration_seconds: int


def _parse_admin_ids(raw: str) -> tuple[int, ...]:
    ids: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if part:
            ids.append(int(part))
    return tuple(ids)


def get_settings() -> Settings:
    bot_token = os.environ.get("BOT_TOKEN", "")
    if not bot_token:
        raise RuntimeError("BOT_TOKEN не задан")

    database_url = os.environ.get(
        "DATABASE_URL",
        "postgresql+asyncpg://afisha:password@localhost:5432/afisha_tickets",
    )

    return Settings(
        bot_token=bot_token,
        database_url=database_url,
        super_admin_id=int(os.environ.get("SUPER_ADMIN_ID", "5498976897")),
        admin_ids=_parse_admin_ids(os.environ.get("ADMIN_IDS", "7846597750,1368175520")),
        parse_interval_seconds=int(os.environ.get("PARSE_INTERVAL_SECONDS", "60")),
        appearance_interval_seconds=int(os.environ.get("APPEARANCE_INTERVAL_SECONDS", "30")),
        appearance_duration_seconds=int(os.environ.get("APPEARANCE_DURATION_SECONDS", "3600")),
    )


def all_admin_ids(settings: Settings) -> tuple[int, ...]:
    return (settings.super_admin_id,) + settings.admin_ids
