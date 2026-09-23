from __future__ import annotations

from datetime import datetime, timezone

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from src.config import Settings
from src.db.models import AppearanceAlert, TrackedEvent
from src.db.repository import Database
from src.parser.aggregator import TicketSnapshot, format_change_line, format_price_line
from src.services.notification_filters import filtered_diff, filter_event_snapshot


class NotificationService:
    def __init__(self, bot: Bot, db: Database, settings: Settings) -> None:
        self.bot = bot
        self.db = db
        self.settings = settings

    async def send_change_notification(
        self,
        event: TrackedEvent,
        previous: TicketSnapshot | None,
        current: TicketSnapshot,
        telegram_id: int,
    ) -> None:
        excluded = set(event.notify_excluded_sectors or [])
        changes = filtered_diff(
            previous,
            current,
            price_min_rub=event.notify_price_min_rub,
            price_max_rub=event.notify_price_max_rub,
            excluded_sectors=excluded,
        )
        if not changes:
            return

        filtered = filter_event_snapshot(event, current)
        lines = ["🔔 Изменения по билетам", ""]
        if self._has_active_filters(event):
            lines.append(self._filter_summary(event))
            lines.append("")
        for lot, delta in changes:
            lines.append(format_change_line(lot, delta))
        lines.extend(["", event.title, self._format_event_meta(event), ""])
        for lot in filtered.lots:
            lines.append(format_price_line(lot))
        lines.extend(["", f"Всего (с учётом фильтра): {filtered.total_count} билетов", "", f"🔗 {event.source_url}"])

        await self.bot.send_message(telegram_id, "\n".join(lines))

    async def send_appearance_notification(
        self,
        event: TrackedEvent,
        current: TicketSnapshot,
        alert: AppearanceAlert,
    ) -> None:
        filtered = filter_event_snapshot(event, current)
        lines = [
            "🎟 Появились билеты!",
            "",
            event.title,
            self._format_event_meta(event),
            "",
        ]
        if self._has_active_filters(event):
            lines.append(self._filter_summary(event))
            lines.append("")
        for lot in filtered.lots:
            lines.append(format_price_line(lot))
        lines.extend(["", f"Всего (с учётом фильтра): {filtered.total_count} билетов", "", f"🔗 {event.source_url}"])

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Я увидел",
                        callback_data=f"seen:{alert.id}",
                    )
                ]
            ]
        )
        await self.bot.send_message(
            alert.user.telegram_id,
            "\n".join(lines),
            reply_markup=keyboard,
        )
        await self.db.mark_appearance_sent(alert.id)

    @staticmethod
    def _format_event_meta(event: TrackedEvent) -> str:
        date_part = event.session_datetime.replace("T", " ").split("+")[0]
        venue = event.venue_name
        if event.venue_address:
            venue = f"{venue}, {event.venue_address}"
        return f"📅 {date_part} — {venue}"

    @staticmethod
    def build_episode_key(snapshot: TicketSnapshot) -> str:
        return f"{snapshot.sale_status}:{snapshot.total_count}:{datetime.now(timezone.utc).date().isoformat()}"

    @staticmethod
    def _has_active_filters(event: TrackedEvent) -> bool:
        return bool(
            event.notify_price_min_rub is not None
            or event.notify_price_max_rub is not None
            or (event.notify_excluded_sectors or [])
        )

    @staticmethod
    def _filter_summary(event: TrackedEvent) -> str:
        parts: list[str] = []
        if event.notify_price_min_rub is not None:
            parts.append(f"от {event.notify_price_min_rub:,} ₽".replace(",", " "))
        if event.notify_price_max_rub is not None:
            parts.append(f"до {event.notify_price_max_rub:,} ₽".replace(",", " "))
        excluded = event.notify_excluded_sectors or []
        if excluded:
            parts.append(f"без секторов: {', '.join(excluded[:5])}" + ("…" if len(excluded) > 5 else ""))
        return "Фильтр: " + "; ".join(parts) + " (цена с учётом сбора)"
