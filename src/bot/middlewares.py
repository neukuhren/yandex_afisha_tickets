from __future__ import annotations

from aiogram import BaseMiddleware, Bot
from aiogram.types import TelegramObject
from typing import Any, Awaitable, Callable

from src.config import Settings, all_admin_ids


class AdminOnlyMiddleware(BaseMiddleware):
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.allowed_ids = set(all_admin_ids(settings))

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        from aiogram.types import CallbackQuery, Message

        user = None
        if isinstance(event, Message):
            user = event.from_user
        elif isinstance(event, CallbackQuery):
            user = event.from_user

        if user and user.id not in self.allowed_ids:
            bot: Bot = data["bot"]
            if isinstance(event, Message):
                await event.answer("У вас нет доступа к этому боту.")
            elif isinstance(event, CallbackQuery):
                await event.answer("Нет доступа", show_alert=True)
            return None

        return await handler(event, data)
