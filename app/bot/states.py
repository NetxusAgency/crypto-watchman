from aiogram.fsm.state import StatesGroup, State


class PortfolioStates(StatesGroup):
    waiting_for_asset = State()


class AlertStates(StatesGroup):
    waiting_symbol = State()
    waiting_type = State()
    waiting_target = State()


class WhaleStates(StatesGroup):
    waiting_for_symbols = State()


class AssistantStates(StatesGroup):
    waiting_for_symbol = State()
    waiting_for_strategy_name = State()
    waiting_for_strategy_rules = State()
    waiting_for_strategy_document = State()
