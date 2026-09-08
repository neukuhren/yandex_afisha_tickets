from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from src.config import Settings, all_admin_ids
from src.db.migrations import run_migrations
from src.db.models import AppearanceAlert, Base, BotUser, TicketSnapshotRow, TrackedEvent, UserEventSubscription
from src.parser.aggregator import TicketLot, TicketSnapshot


class Database:
    def __init__(self, settings: Settings) -> None:
        self.engine = create_async_engine(settings.database_url, pool_pre_ping=True)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

    async def init(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await run_migrations(self.engine)

    async def close(self) -> None:
        await self.engine.dispose()

    async def ensure_admins(self, settings: Settings) -> None:
        async with self.session_factory() as session:
            for admin_id in all_admin_ids(settings):
                existing = await session.scalar(
                    select(BotUser).where(BotUser.telegram_id == admin_id)
                )
                if existing:
                    existing.is_super_admin = admin_id == settings.super_admin_id
                    continue
                session.add(
                    BotUser(
                        telegram_id=admin_id,
                        is_super_admin=admin_id == settings.super_admin_id,
                    )
                )
            await session.commit()

    async def get_user_by_telegram_id(self, telegram_id: int) -> BotUser | None:
        async with self.session_factory() as session:
            return await session.scalar(
                select(BotUser).where(BotUser.telegram_id == telegram_id)
            )

    async def list_active_events(self) -> list[TrackedEvent]:
        async with self.session_factory() as session:
            result = await session.scalars(
                select(TrackedEvent).where(TrackedEvent.is_active.is_(True))
            )
            return list(result)

    async def get_event(self, event_id: int) -> TrackedEvent | None:
        async with self.session_factory() as session:
            return await session.scalar(
                select(TrackedEvent).where(TrackedEvent.id == event_id)
            )

    async def get_event_by_url(self, source_url: str) -> TrackedEvent | None:
        async with self.session_factory() as session:
            return await session.scalar(
                select(TrackedEvent).where(TrackedEvent.source_url == source_url)
            )

    async def get_event_by_widget_id(self, widget_event_id: int) -> TrackedEvent | None:
        if widget_event_id <= 0:
            return None
        async with self.session_factory() as session:
            return await session.scalar(
                select(TrackedEvent).where(TrackedEvent.widget_event_id == widget_event_id)
            )

    async def create_event(
        self,
        *,
        source_url: str,
        widget_event_id: int,
        region_id: int,
        client_key: str,
        session_key: str,
        session_id: int,
        title: str,
        venue_name: str,
        venue_address: str,
        session_datetime: str,
        status: str = "active",
        pending_reason: str | None = None,
    ) -> TrackedEvent:
        async with self.session_factory() as session:
            event = TrackedEvent(
                source_url=source_url,
                widget_event_id=widget_event_id,
                region_id=region_id,
                client_key=client_key,
                session_key=session_key,
                session_id=session_id,
                title=title,
                venue_name=venue_name,
                venue_address=venue_address,
                session_datetime=session_datetime,
                status=status,
                pending_reason=pending_reason,
                pending_sessions=None,
            )
            session.add(event)
            await session.flush()

            users = await session.scalars(select(BotUser))
            for user in users:
                session.add(
                    UserEventSubscription(
                        user_id=user.id,
                        event_id=event.id,
                        notifications_enabled=True,
                    )
                )
            await session.commit()
            await session.refresh(event)
            return event

    async def create_pending_event(
        self,
        *,
        source_url: str,
        widget_event_id: int,
        region_id: int,
        client_key: str,
        title: str,
        pending_reason: str,
    ) -> TrackedEvent:
        return await self.create_event(
            source_url=source_url,
            widget_event_id=widget_event_id,
            region_id=region_id,
            client_key=client_key,
            session_key="",
            session_id=0,
            title=title,
            venue_name="",
            venue_address="",
            session_datetime="",
            status="pending",
            pending_reason=pending_reason,
        )

    async def update_event_widget(
        self,
        event_id: int,
        *,
        widget_event_id: int,
        region_id: int,
        client_key: str,
        title: str | None = None,
    ) -> None:
        async with self.session_factory() as session:
            event = await session.scalar(select(TrackedEvent).where(TrackedEvent.id == event_id))
            if not event:
                return
            event.widget_event_id = widget_event_id
            event.region_id = region_id
            event.client_key = client_key
            if title:
                event.title = title
            if event.pending_reason == "no_widget":
                event.pending_reason = "no_sessions"
            await session.commit()

    async def set_pending_sessions(self, event_id: int, sessions: list[dict]) -> None:
        async with self.session_factory() as session:
            event = await session.scalar(select(TrackedEvent).where(TrackedEvent.id == event_id))
            if not event:
                return
            event.pending_sessions = {"items": sessions}
            event.status = "awaiting_session"
            await session.commit()

    async def activate_event_session(
        self,
        event_id: int,
        *,
        session_key: str,
        session_id: int,
        venue_name: str,
        venue_address: str,
        session_datetime: str,
        title: str | None = None,
    ) -> TrackedEvent | None:
        async with self.session_factory() as session:
            event = await session.scalar(select(TrackedEvent).where(TrackedEvent.id == event_id))
            if not event:
                return None
            event.session_key = session_key
            event.session_id = session_id
            event.venue_name = venue_name
            event.venue_address = venue_address
            event.session_datetime = session_datetime
            event.status = "active"
            event.pending_reason = None
            event.pending_sessions = None
            if title:
                event.title = title
            await session.commit()
            await session.refresh(event)
            return event

    async def list_events_with_subscriptions(self, telegram_id: int) -> list[tuple[TrackedEvent, bool]]:
        async with self.session_factory() as session:
            user = await session.scalar(select(BotUser).where(BotUser.telegram_id == telegram_id))
            if not user:
                return []

            result = await session.execute(
                select(TrackedEvent, UserEventSubscription.notifications_enabled)
                .join(
                    UserEventSubscription,
                    UserEventSubscription.event_id == TrackedEvent.id,
                )
                .where(
                    UserEventSubscription.user_id == user.id,
                    TrackedEvent.is_active.is_(True),
                )
                .order_by(TrackedEvent.id)
            )
            return list(result.all())

    async def set_subscription(self, telegram_id: int, event_id: int, enabled: bool) -> bool:
        async with self.session_factory() as session:
            user = await session.scalar(select(BotUser).where(BotUser.telegram_id == telegram_id))
            if not user:
                return False
            subscription = await session.scalar(
                select(UserEventSubscription).where(
                    UserEventSubscription.user_id == user.id,
                    UserEventSubscription.event_id == event_id,
                )
            )
            if not subscription:
                return False
            subscription.notifications_enabled = enabled
            await session.commit()
            return True

    async def get_latest_snapshot(self, event_id: int) -> TicketSnapshot | None:
        async with self.session_factory() as session:
            row = await session.scalar(
                select(TicketSnapshotRow)
                .where(TicketSnapshotRow.event_id == event_id)
                .order_by(TicketSnapshotRow.created_at.desc())
                .limit(1)
            )
            if not row:
                return None
            return _snapshot_from_row(row)

    async def save_snapshot(self, event_id: int, snapshot: TicketSnapshot) -> None:
        async with self.session_factory() as session:
            session.add(
                TicketSnapshotRow(
                    event_id=event_id,
                    sale_status=snapshot.sale_status,
                    total_count=snapshot.total_count,
                    lots={
                        "items": [
                            {
                                "sector": lot.sector,
                                "price_kopecks": lot.price_kopecks,
                                "fee_kopecks": lot.fee_kopecks,
                                "count": lot.count,
                            }
                            for lot in snapshot.lots
                        ]
                    },
                )
            )
            await session.commit()

    async def list_notification_recipients(self, event_id: int) -> list[int]:
        async with self.session_factory() as session:
            result = await session.execute(
                select(BotUser.telegram_id)
                .join(UserEventSubscription, UserEventSubscription.user_id == BotUser.id)
                .where(
                    UserEventSubscription.event_id == event_id,
                    UserEventSubscription.notifications_enabled.is_(True),
                )
            )
            return [row[0] for row in result.all()]

    async def get_active_appearance_alerts(self) -> list[AppearanceAlert]:
        now = datetime.now(timezone.utc)
        async with self.session_factory() as session:
            result = await session.scalars(
                select(AppearanceAlert)
                .options(selectinload(AppearanceAlert.event), selectinload(AppearanceAlert.user))
                .where(
                    AppearanceAlert.is_active.is_(True),
                    AppearanceAlert.acknowledged_at.is_(None),
                    AppearanceAlert.expires_at > now,
                )
            )
            return list(result)

    async def create_appearance_alerts(
        self,
        event_id: int,
        episode_key: str,
        telegram_ids: list[int],
        duration_seconds: int,
    ) -> None:
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=duration_seconds)
        async with self.session_factory() as session:
            users = await session.scalars(
                select(BotUser).where(BotUser.telegram_id.in_(telegram_ids))
            )
            for user in users:
                existing = await session.scalar(
                    select(AppearanceAlert).where(
                        AppearanceAlert.user_id == user.id,
                        AppearanceAlert.event_id == event_id,
                        AppearanceAlert.episode_key == episode_key,
                    )
                )
                if existing:
                    existing.is_active = True
                    existing.acknowledged_at = None
                    existing.started_at = now
                    existing.expires_at = expires_at
                    existing.last_sent_at = None
                    continue
                session.add(
                    AppearanceAlert(
                        user_id=user.id,
                        event_id=event_id,
                        episode_key=episode_key,
                        started_at=now,
                        expires_at=expires_at,
                        is_active=True,
                    )
                )
            await session.commit()

    async def acknowledge_appearance(self, alert_id: int, telegram_id: int) -> bool:
        now = datetime.now(timezone.utc)
        async with self.session_factory() as session:
            alert = await session.scalar(
                select(AppearanceAlert)
                .join(BotUser)
                .where(
                    AppearanceAlert.id == alert_id,
                    BotUser.telegram_id == telegram_id,
                )
            )
            if not alert:
                return False
            alert.acknowledged_at = now
            alert.is_active = False
            await session.commit()
            return True

    async def mark_appearance_sent(self, alert_id: int) -> None:
        now = datetime.now(timezone.utc)
        async with self.session_factory() as session:
            await session.execute(
                update(AppearanceAlert)
                .where(AppearanceAlert.id == alert_id)
                .values(last_sent_at=now)
            )
            await session.commit()

    async def deactivate_expired_alerts(self) -> None:
        now = datetime.now(timezone.utc)
        async with self.session_factory() as session:
            await session.execute(
                update(AppearanceAlert)
                .where(
                    AppearanceAlert.is_active.is_(True),
                    AppearanceAlert.expires_at <= now,
                )
                .values(is_active=False)
            )
            await session.commit()


def _snapshot_from_row(row: TicketSnapshotRow) -> TicketSnapshot:
    lots = [
        TicketLot(
            sector=item["sector"],
            price_kopecks=item["price_kopecks"],
            fee_kopecks=item["fee_kopecks"],
            count=item["count"],
        )
        for item in row.lots.get("items", [])
    ]
    return TicketSnapshot(lots=lots, sale_status=row.sale_status, total_count=row.total_count)
