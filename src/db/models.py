from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class TrackedEvent(Base):
    __tablename__ = "tracked_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_url: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    widget_event_id: Mapped[int] = mapped_column(Integer, nullable=False)
    region_id: Mapped[int] = mapped_column(Integer, nullable=False)
    client_key: Mapped[str] = mapped_column(String(64), nullable=False)
    session_key: Mapped[str] = mapped_column(String(128), nullable=False)
    session_id: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    venue_name: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    venue_address: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    session_datetime: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    pending_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pending_sessions: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    snapshots: Mapped[list[TicketSnapshotRow]] = relationship(back_populates="event")
    subscriptions: Mapped[list[UserEventSubscription]] = relationship(back_populates="event")
    appearance_alerts: Mapped[list[AppearanceAlert]] = relationship(back_populates="event")


class TicketSnapshotRow(Base):
    __tablename__ = "ticket_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("tracked_events.id", ondelete="CASCADE"), index=True)
    sale_status: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    total_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lots: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    event: Mapped[TrackedEvent] = relationship(back_populates="snapshots")


class BotUser(Base):
    __tablename__ = "bot_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    is_super_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    subscriptions: Mapped[list[UserEventSubscription]] = relationship(back_populates="user")
    appearance_alerts: Mapped[list[AppearanceAlert]] = relationship(back_populates="user")


class UserEventSubscription(Base):
    __tablename__ = "user_event_subscriptions"
    __table_args__ = (UniqueConstraint("user_id", "event_id", name="uq_user_event"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("bot_users.id", ondelete="CASCADE"), index=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("tracked_events.id", ondelete="CASCADE"), index=True)
    notifications_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    user: Mapped[BotUser] = relationship(back_populates="subscriptions")
    event: Mapped[TrackedEvent] = relationship(back_populates="subscriptions")


class AppearanceAlert(Base):
    __tablename__ = "appearance_alerts"
    __table_args__ = (
        UniqueConstraint("user_id", "event_id", "episode_key", name="uq_appearance_episode"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("bot_users.id", ondelete="CASCADE"), index=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("tracked_events.id", ondelete="CASCADE"), index=True)
    episode_key: Mapped[str] = mapped_column(String(64), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    user: Mapped[BotUser] = relationship(back_populates="appearance_alerts")
    event: Mapped[TrackedEvent] = relationship(back_populates="appearance_alerts")
