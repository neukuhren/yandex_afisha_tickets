from __future__ import annotations

import logging

from aiogram import Bot

from src.config import Settings
from src.db.models import TrackedEvent
from src.db.repository import Database
from src.parser.afisha_client import AfishaClient
from src.parser.aggregator import TicketSnapshot
from src.services.notifier import NotificationService

logger = logging.getLogger(__name__)


class PendingEventService:
    def __init__(
        self,
        bot: Bot,
        db: Database,
        settings: Settings,
        parser: AfishaClient,
    ) -> None:
        self.bot = bot
        self.db = db
        self.settings = settings
        self.parser = parser

    async def process(self, event: TrackedEvent) -> None:
        if event.status == "active" and event.session_key:
            return

        if event.widget_event_id <= 0 and "afisha.yandex.ru" in event.source_url:
            parsed = await self.parser.try_resolve_widget_from_afisha(event.source_url)
            if parsed:
                meta = await self.parser.get_event_meta(
                    parsed.event_id,
                    parsed.region_id,
                    parsed.client_key,
                )
                await self.db.update_event_widget(
                    event.id,
                    widget_event_id=parsed.event_id,
                    region_id=parsed.region_id,
                    client_key=meta.client_key,
                    title=meta.name,
                )
                event = await self.db.get_event(event.id)
                if not event:
                    return

        if not event or event.widget_event_id <= 0:
            return

        meta, sessions = await self.parser.discover_sessions(
            event.widget_event_id,
            event.region_id,
            event.client_key or None,
        )
        if meta.name and meta.name != event.title:
            await self.db.update_event_widget(
                event.id,
                widget_event_id=event.widget_event_id,
                region_id=event.region_id,
                client_key=meta.client_key,
                title=meta.name,
            )

        if not sessions:
            return

        if len(sessions) == 1:
            session = sessions[0]
            await self.db.activate_event_session(
                event.id,
                session_key=session.key,
                session_id=session.session_id,
                venue_name=session.venue_name,
                venue_address=session.venue_address,
                session_datetime=session.session_date,
                title=meta.name,
            )
            await self.bot.send_message(
                self.settings.super_admin_id,
                (
                    f"✅ Для события «{meta.name}» появился сеанс.\n"
                    f"Мониторинг билетов запущен автоматически.\n"
                    f"📅 {session.session_date}\n"
                    f"📍 {session.venue_name}"
                ),
            )
            return

        if event.status != "awaiting_session":
            from src.bot.keyboards import sessions_keyboard

            await self.db.set_pending_sessions(
                event.id,
                [session.__dict__ for session in sessions],
            )
            await self.bot.send_message(
                self.settings.super_admin_id,
                (
                    f"🗓 У события «{meta.name}» появились сеансы.\n"
                    f"Выберите сеанс для мониторинга билетов:"
                ),
                reply_markup=sessions_keyboard(
                    sessions,
                    callback_prefix=f"pick_session:{event.id}",
                ),
            )
