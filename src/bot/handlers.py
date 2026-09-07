from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import BotCommand, BotCommandScopeChat, CallbackQuery, Message

from src.bot.keyboards import events_keyboard, sessions_keyboard
from src.bot.states import AddEventStates
from src.config import Settings
from src.db.repository import Database
from src.parser.afisha_client import AfishaClient, AfishaParserError

logger = logging.getLogger(__name__)


def build_router(
    settings: Settings,
    db: Database,
    parser: AfishaClient,
) -> Router:
    router = Router()

    @router.message(CommandStart())
    async def cmd_start(message: Message) -> None:
        await message.answer(
            "Бот мониторинга билетов Яндекс Афиши.\n"
            "Команды:\n"
            "/events — управление оповещениями по событиям"
        )
        if message.from_user and message.from_user.id == settings.super_admin_id:
            await message.answer("Суперадмин: /add_url — добавить событие для отслеживания")

    @router.message(Command("events"))
    async def cmd_events(message: Message) -> None:
        if not message.from_user:
            return
        events = await db.list_events_with_subscriptions(message.from_user.id)
        if not events:
            await message.answer("Пока нет отслеживаемых событий.")
            return
        await message.answer(
            "Нажмите на событие, чтобы включить или выключить оповещения:",
            reply_markup=events_keyboard(events),
        )

    @router.callback_query(F.data.startswith("toggle:"))
    async def toggle_subscription(callback: CallbackQuery) -> None:
        if not callback.from_user or not callback.data:
            return
        event_id = int(callback.data.split(":", 1)[1])
        events = await db.list_events_with_subscriptions(callback.from_user.id)
        current = next((enabled for event, enabled in events if event.id == event_id), None)
        if current is None:
            await callback.answer("Событие не найдено", show_alert=True)
            return
        await db.set_subscription(callback.from_user.id, event_id, not current)
        updated = await db.list_events_with_subscriptions(callback.from_user.id)
        if callback.message:
            await callback.message.edit_reply_markup(reply_markup=events_keyboard(updated))
        await callback.answer("Настройка обновлена")

    @router.callback_query(F.data.startswith("seen:"))
    async def acknowledge_seen(callback: CallbackQuery) -> None:
        if not callback.from_user or not callback.data:
            return
        alert_id = int(callback.data.split(":", 1)[1])
        success = await db.acknowledge_appearance(alert_id, callback.from_user.id)
        if success:
            await callback.answer("Уведомления по этому появлению остановлены")
            if callback.message:
                await callback.message.edit_reply_markup(reply_markup=None)
        else:
            await callback.answer("Не удалось подтвердить", show_alert=True)

    @router.message(Command("add_url"))
    async def cmd_add_url(message: Message, state: FSMContext) -> None:
        if not message.from_user or message.from_user.id != settings.super_admin_id:
            return
        await state.set_state(AddEventStates.waiting_for_url)
        await message.answer("Отправьте ссылку на событие Яндекс Афиши или виджет.")

    @router.message(AddEventStates.waiting_for_url)
    async def process_url(message: Message, state: FSMContext) -> None:
        if not message.text:
            await message.answer("Нужна ссылка текстом.")
            return

        url = message.text.strip()
        try:
            if "widget.afisha.yandex.ru" in url:
                parsed = parser.parse_widget_url(url)
            else:
                parsed = await parser.resolve_afisha_url(url)

            meta = await parser.get_event_meta(
                parsed.event_id,
                parsed.region_id,
                parsed.client_key,
            )
            if not meta.presentation_dates:
                raise AfishaParserError("У события нет доступных дат сеансов")

            date_from = meta.presentation_dates[0]
            date_to = meta.presentation_dates[-1]
            sessions = await parser.list_sessions(
                meta.event_id,
                meta.region_id,
                meta.client_key,
                date_from,
                date_to,
            )
            if not sessions:
                raise AfishaParserError("Не найдено сеансов для выбора")

            await state.update_data(
                source_url=url,
                widget_event_id=meta.event_id,
                region_id=meta.region_id,
                client_key=meta.client_key,
                title=meta.name,
                sessions=[session.__dict__ for session in sessions],
            )
            await state.set_state(AddEventStates.choosing_session)
            await message.answer(
                "Выберите сеанс для отслеживания:",
                reply_markup=sessions_keyboard(sessions),
            )
        except AfishaParserError as exc:
            await message.answer(f"Ошибка: {exc}")
        except Exception:
            logger.exception("Ошибка добавления URL")
            await message.answer("Не удалось обработать ссылку. Проверьте формат.")

    @router.callback_query(AddEventStates.choosing_session, F.data.startswith("session:"))
    async def choose_session(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.data:
            return
        action = callback.data.split(":", 1)[1]
        if action == "cancel":
            await state.clear()
            if callback.message:
                await callback.message.edit_text("Добавление отменено.")
            await callback.answer()
            return

        data = await state.get_data()
        sessions_data = data.get("sessions", [])
        index = int(action)
        if index < 0 or index >= len(sessions_data):
            await callback.answer("Некорректный сеанс", show_alert=True)
            return

        session = sessions_data[index]
        existing = await db.get_event_by_url(data["source_url"])
        if existing:
            if callback.message:
                await callback.message.edit_text("Это событие уже отслеживается.")
            await state.clear()
            await callback.answer()
            return

        event = await db.create_event(
            source_url=data["source_url"],
            widget_event_id=data["widget_event_id"],
            region_id=data["region_id"],
            client_key=data["client_key"],
            session_key=session["key"],
            session_id=session["session_id"],
            title=data["title"],
            venue_name=session["venue_name"],
            venue_address=session["venue_address"],
            session_datetime=session["session_date"],
        )
        await state.clear()
        if callback.message:
            await callback.message.edit_text(
                f"Событие добавлено: {event.title}\n"
                f"Сеанс: {event.session_datetime}\n"
                f"Площадка: {event.venue_name}"
            )
        await callback.answer("Добавлено")

    return router


async def setup_bot_commands(bot, settings: Settings) -> None:
    default_commands = [
        BotCommand(command="start", description="Запуск бота"),
        BotCommand(command="events", description="Управление оповещениями"),
    ]
    await bot.set_my_commands(default_commands)

    super_admin_commands = default_commands + [
        BotCommand(command="add_url", description="Добавить событие"),
    ]
    await bot.set_my_commands(
        super_admin_commands,
        scope=BotCommandScopeChat(chat_id=settings.super_admin_id),
    )
