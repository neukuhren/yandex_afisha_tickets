from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from src.db.models import TrackedEvent
from src.parser.afisha_client import SessionInfo


def events_keyboard(events: list[tuple[TrackedEvent, bool]]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for event, enabled in events:
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


def sessions_keyboard(sessions: list[SessionInfo]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for index, session in enumerate(sessions):
        date = session.session_date.replace("T", " ").split("+")[0]
        label = f"{date} | {session.venue_name} | {session.sale_status}"
        rows.append(
            [
                InlineKeyboardButton(
                    text=label[:60],
                    callback_data=f"session:{index}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="Отмена", callback_data="session:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
