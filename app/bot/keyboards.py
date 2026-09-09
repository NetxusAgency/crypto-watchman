from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.button(text="📊 Portfolio")
    builder.button(text="🔔 Alerts")
    builder.button(text="📈 Analytics")
    builder.button(text="📢 Sentiment")
    builder.button(text="🐋 Whales")
    builder.button(text="🧠 Digest")
    builder.button(text="⚙️ Settings")
    builder.button(text="❓ Help")
    builder.adjust(3, 3, 2)
    return builder.as_markup(resize_keyboard=True, input_field_placeholder="Choose an option...")


def back_button() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="◀ Back", callback_data="menu_main")]]
    )


def cancel_button() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Cancel", callback_data="menu_main")]]
    )


def portfolio_actions_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ Add Asset", callback_data="portfolio_add")],
            [InlineKeyboardButton(text="✖ Remove Asset", callback_data="portfolio_remove_list")],
            [InlineKeyboardButton(text="◀ Back", callback_data="menu_main")],
        ]
    )


def asset_list_keyboard(symbols: list[str]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for sym in symbols:
        builder.button(text=sym, callback_data=f"portfolio_remove:{sym}")
    builder.adjust(2)
    builder.row(InlineKeyboardButton(text="◀ Back", callback_data="menu_portfolio"))
    return builder.as_markup()


def confirm_remove_keyboard(symbol: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Yes, remove", callback_data=f"portfolio_remove_confirm:{symbol}")],
            [InlineKeyboardButton(text="❌ No", callback_data="menu_portfolio")],
        ]
    )


def alerts_actions_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ Add Alert", callback_data="alert_add_choose_asset")],
            [InlineKeyboardButton(text="✖ Remove Alert", callback_data="alert_remove_list")],
            [InlineKeyboardButton(text="◀ Back", callback_data="menu_main")],
        ]
    )


def alert_types_keyboard(symbol: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📈 Above price", callback_data=f"alert_type:{symbol}:above")],
            [InlineKeyboardButton(text="📉 Below price", callback_data=f"alert_type:{symbol}:below")],
            [InlineKeyboardButton(text="📊 Move %", callback_data=f"alert_type:{symbol}:move")],
            [InlineKeyboardButton(text="💹 Volume", callback_data=f"alert_type:{symbol}:volume")],
            [InlineKeyboardButton(text="🌊 Volatility", callback_data=f"alert_type:{symbol}:volatility")],
            [InlineKeyboardButton(text="📢 Sentiment", callback_data=f"alert_type:{symbol}:sentiment")],
            [InlineKeyboardButton(text="🐋 Whale", callback_data=f"alert_type:{symbol}:whale")],
            [InlineKeyboardButton(text="◀ Back", callback_data="menu_alerts")],
        ]
    )


def alert_asset_list_keyboard(symbols: list[str]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for sym in symbols:
        builder.button(text=sym, callback_data=f"alert_add_type:{sym}")
    builder.adjust(2)
    builder.row(InlineKeyboardButton(text="◀ Back", callback_data="menu_alerts"))
    return builder.as_markup()


def alert_list_keyboard(alerts: list) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for a in alerts:
        label = f"{a.symbol} — {a.alert_type.replace('price_', '').replace('_', ' ').title()}"
        builder.button(text=label, callback_data=f"alert_remove:{a.id}")
    builder.adjust(1)
    builder.row(InlineKeyboardButton(text="◀ Back", callback_data="menu_alerts"))
    return builder.as_markup()


def confirm_alert_remove_keyboard(alert_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Yes, remove", callback_data=f"alert_remove_confirm:{alert_id}")],
            [InlineKeyboardButton(text="❌ No", callback_data="menu_alerts")],
        ]
    )
