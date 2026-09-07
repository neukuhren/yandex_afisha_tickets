from __future__ import annotations

from datetime import datetime, timezone

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from src.config import Settings
from src.db.models import AppearanceAlert, TrackedEvent
from src.db.repository import Database
from src.parser.aggregator import TicketSnapshot, diff_snapshots, format_change_line, format_price_line


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
        changes = diff_snapshots(previous, current)
        if not changes:
            return

        lines = ["🔔 Изменения по билетам", ""]
        for lot, delta in changes:
            lines.append(format_change_line(lot, delta))
        lines.extend(["", event.title, self._format_event_meta(event), ""])
        for lot in current.lots:
            lines.append(format_price_line(lot))
        lines.extend(["", f"Всего: {current.total_count} билетов", "", f"🔗 {event.source_url}"])

        await self.bot.send_message(telegram_id, "\n".join(lines))

    async def send_appearance_notification(
        self,
        event: TrackedEvent,
        current: TicketSnapshot,
        alert: AppearanceAlert,
    ) -> None:
        lines = [
            "🎟 Появились билеты!",
            "",
            event.title,
            self._format_event_meta(event),
            "",
        ]
        for lot in current.lots:
            lines.append(format_price_line(lot))
        lines.extend(["", f"Всего: {current.total_count} билетов", "", f"🔗 {event.source_url}"])

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
