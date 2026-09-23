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


def admin_events_keyboard(events: list[TrackedEvent]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for event in events:
        status = "⏳" if event.status != "active" else "✅"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{status} {event.title[:45]}",
                    callback_data=f"manage:{event.id}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def manage_event_keyboard(event_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⚙️ Фильтры оповещений", callback_data=f"filt:{event_id}")],
            [InlineKeyboardButton(text="🗑 Удалить событие", callback_data=f"delask:{event_id}")],
            [InlineKeyboardButton(text="« К списку", callback_data="manage:list")],
        ]
    )


def delete_confirm_keyboard(event_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Да, удалить", callback_data=f"delok:{event_id}"),
                InlineKeyboardButton(text="Отмена", callback_data=f"manage:{event_id}"),
            ]
        ]
    )


def filters_menu_keyboard(event: TrackedEvent) -> InlineKeyboardMarkup:
    min_label = f"{event.notify_price_min_rub} ₽" if event.notify_price_min_rub is not None else "не задан"
    max_label = f"{event.notify_price_max_rub} ₽" if event.notify_price_max_rub is not None else "не задан"
    excluded = event.notify_excluded_sectors or []
    sec_label = f"{len(excluded)} исключено" if excluded else "все включены"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"Мин. цена: {min_label}", callback_data=f"fmin:{event.id}")],
            [InlineKeyboardButton(text=f"Макс. цена: {max_label}", callback_data=f"fmax:{event.id}")],
            [InlineKeyboardButton(text=f"Сектора: {sec_label}", callback_data=f"fsec:{event.id}")],
            [InlineKeyboardButton(text="« Назад", callback_data=f"manage:{event.id}")],
        ]
    )


def price_bound_actions_keyboard(event_id: int, kind: str) -> InlineKeyboardMarkup:
    clear_data = f"fclr{kind}:{event_id}"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Сбросить порог", callback_data=clear_data)],
            [InlineKeyboardButton(text="« Назад", callback_data=f"filt:{event_id}")],
        ]
    )


def sectors_keyboard(event: TrackedEvent) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    excluded = set(event.notify_excluded_sectors or [])
    sectors = event.known_sectors or []
    for index, sector in enumerate(sectors):
        mark = "❌" if sector in excluded else "✅"
        label = f"{mark} {sector}"
        if len(label) > 60:
            label = label[:57] + "…"
        rows.append(
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"fsect:{event.id}:{index}",
                )
            ]
        )
    if not sectors:
        rows.append(
            [
                InlineKeyboardButton(
                    text="Сектора появятся после первого парсинга",
                    callback_data=f"filt:{event.id}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="« К фильтрам", callback_data=f"filt:{event.id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def post_add_filters_keyboard(event_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⚙️ Настроить фильтры", callback_data=f"filt:{event_id}")],
            [InlineKeyboardButton(text="Пропустить", callback_data=f"fskip:{event_id}")],
        ]
    )


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
