from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class AddEventStates(StatesGroup):
    waiting_for_url = State()
    choosing_session = State()
