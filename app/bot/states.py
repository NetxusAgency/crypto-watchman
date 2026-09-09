from aiogram.fsm.state import StatesGroup, State


class PortfolioStates(StatesGroup):
    waiting_for_asset = State()


class AlertStates(StatesGroup):
    waiting_symbol = State()
    waiting_type = State()
    waiting_target = State()
