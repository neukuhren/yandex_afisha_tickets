from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from aiogram import Bot

from src.config import Settings
from src.db.repository import Database
from src.parser.afisha_client import AfishaClient
from src.parser.aggregator import TicketSnapshot
from src.services.notifier import NotificationService

logger = logging.getLogger(__name__)


class MonitorService:
    def __init__(
        self,
        bot: Bot,
        db: Database,
        settings: Settings,
        parser: AfishaClient,
        notifier: NotificationService,
    ) -> None:
        self.bot = bot
        self.db = db
        self.settings = settings
        self.parser = parser
        self.notifier = notifier
        self._parse_task: asyncio.Task | None = None
        self._appearance_task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

    def start(self) -> None:
        self._stop_event.clear()
        self._parse_task = asyncio.create_task(self._parse_loop(), name="parse-loop")
        self._appearance_task = asyncio.create_task(
            self._appearance_loop(), name="appearance-loop"
        )

    async def stop(self) -> None:
        self._stop_event.set()
        tasks = [task for task in (self._parse_task, self._appearance_task) if task]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _parse_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self._run_parse_cycle()
            except Exception:
                logger.exception("Ошибка цикла парсинга")
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.settings.parse_interval_seconds,
                )
            except asyncio.TimeoutError:
                continue

    async def _appearance_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self._run_appearance_cycle()
            except Exception:
                logger.exception("Ошибка цикла повторных уведомлений")
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.settings.appearance_interval_seconds,
                )
            except asyncio.TimeoutError:
                continue

    async def _run_parse_cycle(self) -> None:
        events = await self.db.list_active_events()
        for event in events:
            try:
                await self._process_event(event.id)
            except Exception:
                logger.exception("Ошибка парсинга события %s", event.id)

    async def _process_event(self, event_id: int) -> None:
        event = await self.db.get_event(event_id)
        if not event:
            return

        sessions = await self.parser.list_sessions(
            event.widget_event_id,
            event.region_id,
            event.client_key,
            event.session_datetime[:10],
            event.session_datetime[:10],
        )
        session = next((item for item in sessions if item.key == event.session_key), None)
        if not session:
            logger.warning("Сеанс не найден для события %s", event.id)
            return

        current = await self.parser.fetch_ticket_snapshot(
            event.session_key,
            event.client_key,
            session.sale_status,
            session.available_seat_count,
        )
        previous = await self.db.get_latest_snapshot(event.id)

        if previous is None:
            await self.db.save_snapshot(event.id, current)
            return

        appearance_triggered = self._is_appearance(previous, current)
        changed = previous.as_map() != current.as_map() or previous.sale_status != current.sale_status

        await self.db.save_snapshot(event.id, current)

        if not changed:
            return

        recipients = await self.db.list_notification_recipients(event.id)
        if appearance_triggered:
            episode_key = self.notifier.build_episode_key(current)
            await self.db.create_appearance_alerts(
                event.id,
                episode_key,
                recipients,
                self.settings.appearance_duration_seconds,
            )
            alerts = await self.db.get_active_appearance_alerts()
            event_alerts = [alert for alert in alerts if alert.event_id == event.id]
            for alert in event_alerts:
                await self.notifier.send_appearance_notification(event, current, alert)
            return

        for telegram_id in recipients:
            await self.notifier.send_change_notification(event, previous, current, telegram_id)

    async def _run_appearance_cycle(self) -> None:
        await self.db.deactivate_expired_alerts()
        alerts = await self.db.get_active_appearance_alerts()
        now = datetime.now(timezone.utc)

        for alert in alerts:
            if alert.acknowledged_at is not None:
                continue
            if alert.last_sent_at:
                elapsed = (now - alert.last_sent_at).total_seconds()
                if elapsed < self.settings.appearance_interval_seconds:
                    continue

            snapshot = await self.db.get_latest_snapshot(alert.event_id)
            if not snapshot or snapshot.total_count <= 0:
                continue

            await self.notifier.send_appearance_notification(alert.event, snapshot, alert)

    @staticmethod
    def _is_appearance(previous: TicketSnapshot, current: TicketSnapshot) -> bool:
        zero_to_positive = previous.total_count == 0 and current.total_count > 0
        status_changed = (
            previous.sale_status in {"no-seats", "closed", "sold-out", "unknown"}
            and current.sale_status == "available"
            and current.total_count > 0
        )
        return zero_to_positive or status_changed
