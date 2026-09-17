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
    builder.button(text="👛 Wallet")
    builder.button(text="🧠 Digest")
    builder.button(text="📰 News")
    builder.button(text="🎯 Assistant")
    builder.button(text="⚙️ Settings")
    builder.button(text="❓ Help")
    builder.adjust(3, 3, 3, 2)
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


def whale_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔧 Set scan coins", callback_data="whale_configure")],
            [InlineKeyboardButton(text="◀ Back", callback_data="menu_main")],
        ]
    )


def wallet_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ Connect Wallet", callback_data="wallet_add")],
            [
                InlineKeyboardButton(text="✖ Remove Wallet", callback_data="wallet_list"),
                InlineKeyboardButton(text="🔄 Refresh", callback_data="wallet_refresh"),
            ],
            [InlineKeyboardButton(text="◀ Back", callback_data="menu_main")],
        ]
    )


def wallet_remove_list_keyboard(wallets: list) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for w in wallets:
        builder.button(text=f"✖ {w.network} · {w.address[:6]}…{w.address[-4:]}", callback_data=f"wallet_remove:{w.id}")
    builder.adjust(1)
    builder.row(InlineKeyboardButton(text="◀ Back", callback_data="wallet_back"))
    return builder.as_markup()


def wallet_remove_confirm_keyboard(wallet_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Yes, remove", callback_data=f"wallet_del:{wallet_id}")],
            [InlineKeyboardButton(text="❌ No", callback_data="wallet_back")],
        ]
    )


def assistant_assets_keyboard(symbols: list[str]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for sym in symbols:
        builder.button(text=sym, callback_data=f"asst_sym:{sym}")
    builder.adjust(2)
    builder.row(InlineKeyboardButton(text="➕ Custom Coin", callback_data="asst_custom"))
    builder.row(InlineKeyboardButton(text="◀ Back", callback_data="menu_main"))
    return builder.as_markup()


def assistant_timeframe_keyboard(symbol: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⚡ 15m", callback_data=f"asst_tf:{symbol}:15m"),
                InlineKeyboardButton(text="⏱ 1h", callback_data=f"asst_tf:{symbol}:1h"),
            ],
            [
                InlineKeyboardButton(text="📊 4h", callback_data=f"asst_tf:{symbol}:4h"),
                InlineKeyboardButton(text="📅 1d", callback_data=f"asst_tf:{symbol}:1d"),
            ],
            [InlineKeyboardButton(text="◀ Back", callback_data="asst_back_assets")],
        ]
    )


def assistant_strategy_keyboard(
    symbol: str,
    timeframe: str,
    user_strategies: list | None = None,
) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="📈 Trend Pullback", callback_data=f"asst_run:{symbol}:{timeframe}:trend_pullback")],
        [InlineKeyboardButton(text="🚀 Breakout Momentum", callback_data=f"asst_run:{symbol}:{timeframe}:breakout")],
        [InlineKeyboardButton(text="🔄 Mean Reversion", callback_data=f"asst_run:{symbol}:{timeframe}:mean_reversion")],
        [InlineKeyboardButton(text="🧭 Full Diagnostic", callback_data=f"asst_run:{symbol}:{timeframe}:general")],
    ]
    for s in (user_strategies or [])[:8]:
        label = s.name if len(s.name) <= 32 else f"{s.name[:31]}…"
        rows.append([
            InlineKeyboardButton(text=f"⭐ {label}", callback_data=f"asst_run:{symbol}:{timeframe}:{s.key}")
        ])
    rows.append([InlineKeyboardButton(text="➕ New Strategy", callback_data="asst_new_strat")])
    rows.append([InlineKeyboardButton(text="📄 Import Strategy Document", callback_data="asst_import_doc")])
    if user_strategies:
        rows.append([InlineKeyboardButton(text="⚙️ Manage Strategies", callback_data="asst_manage_strats")])
    rows.append([InlineKeyboardButton(text="◀ Back", callback_data=f"asst_sym:{symbol}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def assistant_manage_keyboard(strategies: list) -> InlineKeyboardMarkup:
    rows = []
    for s in (strategies or [])[:12]:
        label = s.name if len(s.name) <= 30 else f"{s.name[:29]}…"
        rows.append([
            InlineKeyboardButton(text=f"⭐ {label}", callback_data="asst_noop"),
            InlineKeyboardButton(text="🗑 Delete", callback_data=f"asst_del:{s.key}"),
        ])
    rows.append([InlineKeyboardButton(text="◀ Back", callback_data="asst_back_strats")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
