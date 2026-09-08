from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from src.db.models import TrackedEvent
from src.parser.afisha_client import SessionInfo


def events_keyboard(events: list[tuple[TrackedEvent, bool]]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for event, enabled in events:
        if event.status != "active":
            status = "⏳"
        else:
            status = "🔔" if enabled else "🔕"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{status} {event.title[:40]}",
                    callback_data=f"toggle:{event.id}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def sessions_keyboard(
    sessions: list[SessionInfo],
    callback_prefix: str = "session",
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for index, session in enumerate(sessions):
        date = session.session_date.replace("T", " ").split("+")[0] if session.session_date else "дата уточняется"
        venue = session.venue_name or "площадка уточняется"
        label = f"{date} | {venue} | {session.sale_status}"
        rows.append(
            [
                InlineKeyboardButton(
                    text=label[:60],
                    callback_data=f"{callback_prefix}:{index}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="Отмена", callback_data=f"{callback_prefix}:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
