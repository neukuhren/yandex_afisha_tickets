from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import BotCommand, BotCommandScopeChat, BotCommandScopeDefault, CallbackQuery, Message

from src.bot.keyboards import (
    admin_events_keyboard,
    delete_confirm_keyboard,
    events_keyboard,
    filters_menu_keyboard,
    manage_event_keyboard,
    post_add_filters_keyboard,
    price_bound_actions_keyboard,
    sectors_keyboard,
    sessions_keyboard,
)
from src.bot.states import AddEventStates, FilterPriceStates
from src.config import Settings
from src.db.models import TrackedEvent
from src.db.repository import Database
from src.parser.afisha_client import AfishaClient, AfishaParserError

logger = logging.getLogger(__name__)


def _is_super_admin(user_id: int, settings: Settings) -> bool:
    return user_id == settings.super_admin_id


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
        if message.from_user and _is_super_admin(message.from_user.id, settings):
            await message.answer(
                "Суперадмин:\n"
                "/add_url — добавить событие\n"
                "/manage_events — удаление и фильтры оповещений"
            )

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

    @router.message(Command("manage_events"))
    async def cmd_manage_events(message: Message) -> None:
        if not message.from_user or not _is_super_admin(message.from_user.id, settings):
            return
        events = await db.list_all_tracked_events()
        if not events:
            await message.answer("Нет событий для управления.")
            return
        await message.answer(
            "Выберите событие (удаление и фильтры оповещений):",
            reply_markup=admin_events_keyboard(events),
        )

    @router.message(Command("add_url"))
    async def cmd_add_url(message: Message, state: FSMContext) -> None:
        if not message.from_user or not _is_super_admin(message.from_user.id, settings):
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
            existing = await db.get_event_by_url(url)
            if existing:
                await message.answer("Это событие уже отслеживается.")
                await state.clear()
                return

            resolved = await parser.resolve_event_input(url)
            if resolved.widget_event_id > 0:
                duplicate = await db.get_event_by_widget_id(resolved.widget_event_id)
                if duplicate:
                    await message.answer(
                        f"Событие уже отслеживается как «{duplicate.title}»."
                    )
                    await state.clear()
                    return

            sessions: list = []
            meta = None
            if resolved.widget_event_id > 0:
                meta, sessions = await parser.discover_sessions(
                    resolved.widget_event_id,
                    resolved.region_id,
                    resolved.client_key,
                )

            if sessions:
                await state.update_data(
                    source_url=url,
                    widget_event_id=resolved.widget_event_id,
                    region_id=resolved.region_id,
                    client_key=resolved.client_key or (meta.client_key if meta else ""),
                    title=resolved.title,
                    sessions=[session.__dict__ for session in sessions],
                )
                await state.set_state(AddEventStates.choosing_session)
                await message.answer(
                    "Выберите сеанс для отслеживания:",
                    reply_markup=sessions_keyboard(sessions),
                )
                return

            pending_reason = "no_widget" if resolved.widget_event_id <= 0 else "no_sessions"
            client_key = resolved.client_key or ""
            if resolved.widget_event_id > 0 and meta:
                client_key = meta.client_key

            event = await db.create_pending_event(
                source_url=url,
                widget_event_id=resolved.widget_event_id,
                region_id=resolved.region_id,
                client_key=client_key,
                title=resolved.title,
                pending_reason=pending_reason,
            )
            await state.clear()
            if pending_reason == "no_widget":
                await message.answer(
                    f"Событие «{event.title}» добавлено в режим ожидания.\n"
                    "Бот будет проверять появление виджета билетов, сеансов и билетов."
                )
            else:
                await message.answer(
                    f"Событие «{event.title}» добавлено в режим ожидания.\n"
                    "Сеансы и даты пока не опубликованы — бот будет следить за их появлением "
                    "и за появлением билетов."
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
                f"Площадка: {event.venue_name}\n\n"
                "Можно настроить фильтры оповещений (цена, сектора):",
                reply_markup=post_add_filters_keyboard(event.id),
            )
        await callback.answer("Добавлено")

    @router.callback_query(F.data.startswith("pick_session:"))
    async def pick_pending_session(callback: CallbackQuery) -> None:
        if not callback.from_user or not _is_super_admin(callback.from_user.id, settings):
            await callback.answer("Нет доступа", show_alert=True)
            return
        if not callback.data:
            return

        parts = callback.data.split(":")
        if len(parts) < 3:
            await callback.answer("Некорректные данные", show_alert=True)
            return

        action = parts[-1]
        event_id = int(parts[1])
        if action == "cancel":
            await callback.answer("Отменено")
            if callback.message:
                await callback.message.edit_text("Выбор сеанса отменён.")
            return

        event = await db.get_event(event_id)
        if not event or not event.pending_sessions:
            await callback.answer("Событие не найдено", show_alert=True)
            return

        sessions_data = event.pending_sessions.get("items", [])
        index = int(action)
        if index < 0 or index >= len(sessions_data):
            await callback.answer("Некорректный сеанс", show_alert=True)
            return

        session = sessions_data[index]
        activated = await db.activate_event_session(
            event_id,
            session_key=session["key"],
            session_id=session["session_id"],
            venue_name=session.get("venue_name", ""),
            venue_address=session.get("venue_address", ""),
            session_datetime=session.get("session_date", ""),
            title=event.title,
        )
        activated_id = activated.id if activated else event_id
        if callback.message:
            await callback.message.edit_text(
                f"Событие активировано: {activated.title if activated else event.title}\n"
                f"Сеанс: {session.get('session_date', '')}\n"
                f"Площадка: {session.get('venue_name', '')}\n"
                "Мониторинг билетов запущен.\n\n"
                "Можно настроить фильтры оповещений:",
                reply_markup=post_add_filters_keyboard(activated_id),
            )
        await callback.answer("Сеанс выбран")

    @router.callback_query(F.data == "manage:list")
    async def manage_list(callback: CallbackQuery) -> None:
        if not callback.from_user or not _is_super_admin(callback.from_user.id, settings):
            await callback.answer("Нет доступа", show_alert=True)
            return
        events = await db.list_all_tracked_events()
        if callback.message:
            await callback.message.edit_text(
                "Выберите событие:",
                reply_markup=admin_events_keyboard(events),
            )
        await callback.answer()

    @router.callback_query(F.data.startswith("manage:"))
    async def manage_event(callback: CallbackQuery) -> None:
        if not callback.from_user or not _is_super_admin(callback.from_user.id, settings):
            await callback.answer("Нет доступа", show_alert=True)
            return
        if not callback.data or callback.data == "manage:list":
            return
        event_id = int(callback.data.split(":", 1)[1])
        event = await db.get_event(event_id)
        if not event or not event.is_active:
            await callback.answer("Событие не найдено", show_alert=True)
            return
        if callback.message:
            await callback.message.edit_text(
                f"Управление: {event.title}\n{event.source_url}",
                reply_markup=manage_event_keyboard(event_id),
            )
        await callback.answer()

    @router.callback_query(F.data.startswith("delask:"))
    async def delete_ask(callback: CallbackQuery) -> None:
        if not callback.from_user or not _is_super_admin(callback.from_user.id, settings):
            await callback.answer("Нет доступа", show_alert=True)
            return
        event_id = int(callback.data.split(":", 1)[1])
        event = await db.get_event(event_id)
        if not event:
            await callback.answer("Событие не найдено", show_alert=True)
            return
        if callback.message:
            await callback.message.edit_text(
                f"Удалить «{event.title}» из отслеживания?",
                reply_markup=delete_confirm_keyboard(event_id),
            )
        await callback.answer()

    @router.callback_query(F.data.startswith("delok:"))
    async def delete_confirm(callback: CallbackQuery) -> None:
        if not callback.from_user or not _is_super_admin(callback.from_user.id, settings):
            await callback.answer("Нет доступа", show_alert=True)
            return
        event_id = int(callback.data.split(":", 1)[1])
        event = await db.get_event(event_id)
        if not event:
            await callback.answer("Событие не найдено", show_alert=True)
            return
        await db.deactivate_event(event_id)
        events = await db.list_all_tracked_events()
        if callback.message:
            if events:
                await callback.message.edit_text(
                    f"Событие «{event.title}» удалено.\n\nВыберите событие:",
                    reply_markup=admin_events_keyboard(events),
                )
            else:
                await callback.message.edit_text(f"Событие «{event.title}» удалено. Список пуст.")
        await callback.answer("Удалено")

    @router.callback_query(F.data.startswith("fskip:"))
    async def skip_filters(callback: CallbackQuery) -> None:
        if not callback.from_user or not _is_super_admin(callback.from_user.id, settings):
            await callback.answer("Нет доступа", show_alert=True)
            return
        if callback.message:
            await callback.message.edit_reply_markup(reply_markup=None)
        await callback.answer("Ок")

    async def _refresh_event_sectors(event_id: int) -> TrackedEvent | None:
        event = await db.get_event(event_id)
        if not event:
            return None
        if event.status != "active" or not event.session_key:
            return event
        snapshot = await db.get_latest_snapshot(event_id)
        if snapshot and snapshot.lots:
            await db.update_known_sectors(event_id, [lot.sector for lot in snapshot.lots])
            return await db.get_event(event_id)
        try:
            sessions = await parser.list_sessions(
                event.widget_event_id,
                event.region_id,
                event.client_key,
                event.session_datetime[:10] if event.session_datetime else "",
                event.session_datetime[:10] if event.session_datetime else "",
            )
            session = next((s for s in sessions if s.key == event.session_key), None)
            if not session:
                meta, discovered = await parser.discover_sessions(
                    event.widget_event_id,
                    event.region_id,
                    event.client_key or None,
                )
                session = next((s for s in discovered if s.key == event.session_key), None)
            if session:
                live = await parser.fetch_ticket_snapshot(
                    event.session_key,
                    event.client_key,
                    session.sale_status,
                    session.available_seat_count,
                    widget_event_id=event.widget_event_id,
                    region_id=event.region_id,
                )
                if live.lots:
                    await db.update_known_sectors(event_id, [lot.sector for lot in live.lots])
                    await db.save_snapshot(event_id, live)
                    return await db.get_event(event_id)
        except Exception:
            logger.exception("Не удалось обновить сектора для события %s", event_id)
        return event

    @router.callback_query(F.data.startswith("filt:"))
    async def filters_menu(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.from_user or not _is_super_admin(callback.from_user.id, settings):
            await callback.answer("Нет доступа", show_alert=True)
            return
        await state.clear()
        event_id = int(callback.data.split(":", 1)[1])
        event = await db.get_event(event_id)
        if not event:
            await callback.answer("Событие не найдено", show_alert=True)
            return
        if event.status != "active" or not event.session_key:
            await callback.answer(
                "Мониторинг билетов ещё не запущен (ожидание виджета или сеанса). "
                "Сектора появятся после активации.",
                show_alert=True,
            )
        event = await _refresh_event_sectors(event_id) or event
        status_line = ""
        if event.status != "active" or not event.session_key:
            status_line = (
                f"\n\nСтатус: {event.status}"
                f"{f' ({event.pending_reason})' if event.pending_reason else ''}."
                " Парсинг схемы зала начнётся после привязки виджета и сеанса."
            )
        if callback.message:
            await callback.message.edit_text(
                f"Фильтры оповещений\n{event.title}\n\n"
                "Цена — с учётом сервисного сбора. Снимки в БД хранятся полностью."
                f"{status_line}",
                reply_markup=filters_menu_keyboard(event),
            )
        await callback.answer()

    @router.callback_query(F.data.startswith("fmin:"))
    async def filter_min_start(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.from_user or not _is_super_admin(callback.from_user.id, settings):
            await callback.answer("Нет доступа", show_alert=True)
            return
        event_id = int(callback.data.split(":", 1)[1])
        await state.set_state(FilterPriceStates.waiting_min)
        await state.update_data(filter_event_id=event_id)
        if callback.message:
            await callback.message.edit_text(
                "Введите нижний порог цены в рублях (билеты дешевле не попадут в оповещение).\n"
                "Или нажмите «Сбросить порог».",
                reply_markup=price_bound_actions_keyboard(event_id, "min"),
            )
        await callback.answer()

    @router.callback_query(F.data.startswith("fmax:"))
    async def filter_max_start(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.from_user or not _is_super_admin(callback.from_user.id, settings):
            await callback.answer("Нет доступа", show_alert=True)
            return
        event_id = int(callback.data.split(":", 1)[1])
        await state.set_state(FilterPriceStates.waiting_max)
        await state.update_data(filter_event_id=event_id)
        if callback.message:
            await callback.message.edit_text(
                "Введите верхний порог цены в рублях (дороже — не в оповещении).\n"
                "Или нажмите «Сбросить порог».",
                reply_markup=price_bound_actions_keyboard(event_id, "max"),
            )
        await callback.answer()

    @router.callback_query(F.data.startswith("fclrmin:"))
    async def filter_clear_min(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.from_user or not _is_super_admin(callback.from_user.id, settings):
            await callback.answer("Нет доступа", show_alert=True)
            return
        await state.clear()
        event_id = int(callback.data.split(":", 1)[1])
        event = await db.set_notify_price_bounds(event_id, clear_min=True)
        if not event or not callback.message:
            await callback.answer("Ошибка")
            return
        await callback.message.edit_text(
            "Нижний порог сброшен.",
            reply_markup=filters_menu_keyboard(event),
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("fclrmax:"))
    async def filter_clear_max(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.from_user or not _is_super_admin(callback.from_user.id, settings):
            await callback.answer("Нет доступа", show_alert=True)
            return
        await state.clear()
        event_id = int(callback.data.split(":", 1)[1])
        event = await db.set_notify_price_bounds(event_id, clear_max=True)
        if not event or not callback.message:
            await callback.answer("Ошибка")
            return
        await callback.message.edit_text(
            "Верхний порог сброшен.",
            reply_markup=filters_menu_keyboard(event),
        )
        await callback.answer()

    @router.message(FilterPriceStates.waiting_min)
    async def filter_min_value(message: Message, state: FSMContext) -> None:
        if not message.from_user or not _is_super_admin(message.from_user.id, settings):
            return
        data = await state.get_data()
        event_id = data.get("filter_event_id")
        if not event_id:
            await state.clear()
            return
        try:
            value = int((message.text or "").strip().replace(" ", ""))
        except ValueError:
            await message.answer("Введите целое число рублей.")
            return
        if value < 0:
            await message.answer("Цена не может быть отрицательной.")
            return
        event = await db.set_notify_price_bounds(event_id, price_min_rub=value)
        await state.clear()
        if event:
            await message.answer(
                f"Нижний порог: {value} ₽",
                reply_markup=filters_menu_keyboard(event),
            )

    @router.message(FilterPriceStates.waiting_max)
    async def filter_max_value(message: Message, state: FSMContext) -> None:
        if not message.from_user or not _is_super_admin(message.from_user.id, settings):
            return
        data = await state.get_data()
        event_id = data.get("filter_event_id")
        if not event_id:
            await state.clear()
            return
        try:
            value = int((message.text or "").strip().replace(" ", ""))
        except ValueError:
            await message.answer("Введите целое число рублей.")
            return
        if value < 0:
            await message.answer("Цена не может быть отрицательной.")
            return
        event = await db.set_notify_price_bounds(event_id, price_max_rub=value)
        await state.clear()
        if event:
            await message.answer(
                f"Верхний порог: {value} ₽",
                reply_markup=filters_menu_keyboard(event),
            )

    @router.callback_query(F.data.startswith("fsec:"))
    async def filter_sectors(callback: CallbackQuery) -> None:
        if not callback.from_user or not _is_super_admin(callback.from_user.id, settings):
            await callback.answer("Нет доступа", show_alert=True)
            return
        event_id = int(callback.data.split(":", 1)[1])
        event = await db.get_event(event_id)
        if not event:
            await callback.answer("Событие не найдено", show_alert=True)
            return
        if event.status != "active" or not event.session_key:
            await callback.answer(
                "Сектора будут доступны после запуска мониторинга (событие в ожидании).",
                show_alert=True,
            )
        event = await _refresh_event_sectors(event_id) or event
        hint = ""
        if not (event.known_sectors or []):
            hint = "\n\nСписок секторов пуст: дождитесь первого успешного парсинга схемы зала."
        if callback.message:
            await callback.message.edit_text(
                "Нажмите сектор, чтобы исключить или снова включить в оповещения:\n"
                f"✅ — в оповещениях, ❌ — исключён{hint}",
                reply_markup=sectors_keyboard(event),
            )
        await callback.answer()

    @router.callback_query(F.data.startswith("fsect:"))
    async def filter_sector_toggle(callback: CallbackQuery) -> None:
        if not callback.from_user or not _is_super_admin(callback.from_user.id, settings):
            await callback.answer("Нет доступа", show_alert=True)
            return
        parts = callback.data.split(":")
        event_id = int(parts[1])
        index = int(parts[2])
        event = await db.get_event(event_id)
        if not event:
            await callback.answer("Событие не найдено", show_alert=True)
            return
        sectors = event.known_sectors or []
        if index < 0 or index >= len(sectors):
            await callback.answer("Сектор не найден", show_alert=True)
            return
        event = await db.toggle_sector_exclusion(event_id, sectors[index])
        if not event or not callback.message:
            await callback.answer()
            return
        await callback.message.edit_reply_markup(reply_markup=sectors_keyboard(event))
        await callback.answer("Обновлено")

    return router


async def setup_bot_commands(bot, settings: Settings) -> None:
    default_commands = [
        BotCommand(command="start", description="Запуск бота"),
        BotCommand(command="events", description="Управление оповещениями"),
    ]
    super_admin_commands = default_commands + [
        BotCommand(command="add_url", description="Добавить событие"),
        BotCommand(command="manage_events", description="Удаление и фильтры"),
    ]

    # Меню по умолчанию для всех приватных чатов — без /add_url.
    await bot.set_my_commands(default_commands, scope=BotCommandScopeDefault())

    # Явно задаём меню каждому обычному админу — только /start и /events.
    for admin_id in settings.admin_ids:
        await bot.set_my_commands(
            default_commands,
            scope=BotCommandScopeChat(chat_id=admin_id),
        )

    # Суперадмин видит /add_url только в своём чате.
    await bot.set_my_commands(
        super_admin_commands,
        scope=BotCommandScopeChat(chat_id=settings.super_admin_id),
    )
