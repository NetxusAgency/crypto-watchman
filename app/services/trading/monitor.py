"""Trading monitor — periodic evaluation and paper mark-to-market.

Wiring: scheduler -> `scan_once` (evaluate configured strategies for every
portfolio symbol, route confirmed plans through the PlanManager, commit
approved plans to paper accounts, push Telegram cards) and `mark_all` (fetch
current prices for open paper positions, close STOP/TP, notify).
"""
from __future__ import annotations

import html
import logging

from sqlalchemy import select

from app.database.models import PaperAccount, PaperPosition, Portfolio, User
from app.services import db_service
from app.services.trading.manager import PlanManager, ProposedTrade
from app.services.trading.strategy_engine import preset_specs
from app.services.trading.trade_plan import TradePlan

logger = logging.getLogger("crypto_watchman.trading_monitor")

SCAN_TIMEFRAME = "4h"


def format_paper_card(plan: TradePlan) -> str:
    """Compact HTML card for a new paper trade."""
    direction_icon = "🟢 LONG" if plan.direction == "LONG" else "🔴 SHORT"
    tps = " / ".join(f"TP{i+1} <b>{tp:,.2f}</b>" for i, tp in enumerate(plan.take_profits))
    return (
        f"📈 <b>Paper trade — {html.escape(plan.symbol)} {direction_icon}</b>\n"
        f"Strategy: {html.escape(plan.strategy_name)}\n"
        f"Entry zone: <b>{plan.entry_low:,.2f} – {plan.entry_high:,.2f}</b>\n"
        f"Stop: <b>{plan.stop_loss:,.2f}</b> · R:R {plan.risk_reward}\n"
        f"{tps}\n"
        f"<i>{html.escape(plan.invalidation.split('.')[0])}.</i>"
    )


def format_close_message(symbol: str, reason: str, pnl: float, exit_price: float) -> str:
    icon = "🎯" if pnl > 0 else "🛑"
    sign = "+" if pnl >= 0 else ""
    return (
        f"{icon} <b>Paper {html.escape(symbol)} {reason}</b> @ {exit_price:,.2f}\n"
        f"Realised P&L: <b>{sign}{pnl:,.2f} USD</b>"
    )


async def _watchers(session, symbol: str) -> list[tuple[int, int]]:
    """User id + telegram id for everyone holding `symbol` in their portfolio."""
    rows = (
        await session.execute(
            select(User.id, User.telegram_id)
            .join(Portfolio, Portfolio.user_id == User.id)
            .where(Portfolio.symbol == symbol)
        )
    ).all()
    return [(r.id, r.telegram_id) for r in rows]


async def get_open_symbols(session, account_id: int) -> list[str]:
    rows = (
        await session.execute(
            select(PaperPosition.symbol).where(
                PaperPosition.account_id == account_id, PaperPosition.status == "OPEN"
            )
        )
    ).all()
    return [r[0] for r in rows]


async def get_open_positions(session, account_id: int) -> list[str]:
    return await get_open_symbols(session, account_id)


class TradingMonitor:
    def __init__(self, manager: PlanManager | None = None):
        self.manager = manager or PlanManager()

    def evaluate_presets(self, symbol: str, candles: list) -> list[ProposedTrade]:
        """Pure: run every armed preset over a candle window. No I/O."""
        out = []
        for key, spec in preset_specs().items():
            proposed = self.manager.evaluate(symbol, SCAN_TIMEFRAME, spec, candles)
            if proposed.plan is not None:
                out.append(proposed)
        return out

    async def scan_once(self, session, kline_source) -> dict:
        """Scan all portfolio symbols once; open approved paper trades for holders.

        Returns {"opened": int, "skipped": int} for logging.
        """
        symbols = await db_service.all_portfolio_symbols(session)
        opened = skipped = 0
        for raw in symbols:
            symbol = raw.upper().strip()
            try:
                candles = await kline_source.fetch_candles(symbol, SCAN_TIMEFRAME, limit=200)
                proposals = self.evaluate_presets(symbol, candles)
                if not proposals:
                    continue
                for user_id, telegram_id in await _watchers(session, symbol):
                    for proposed in proposals:
                        opened_one, _ = await self._try_open(session, user_id, telegram_id, proposed)
                        if opened_one:
                            opened += 1
                        else:
                            skipped += 1
            except Exception as e:
                logger.warning(f"Trading scan failed for {symbol}: {e}")
        logger.info(
            f"Trading scan complete: {opened} opened, {skipped} skipped across {len(symbols)} symbols."
        )
        return {"opened": opened, "skipped": skipped}

    async def _try_open(
        self, session, user_id: int, telegram_id: int, proposed: ProposedTrade
    ) -> tuple[bool, str]:
        """Assess via the risk gate and commit when approved. Dedups by symbol."""
        from app.execution.gateway import get_or_create_paper_account

        account = await get_or_create_paper_account(session, user_id)
        open_symbols = await get_open_symbols(session, account.id)
        self.manager.assess(proposed, equity=account.cash, open_symbols=open_symbols)
        if proposed.state != "ready":
            return False, "risk-rejected"
        if proposed.plan.symbol in open_symbols:
            return False, "duplicate-position"
        await self.manager.commit(session, proposed, user_id=user_id)
        if proposed.state != "open":
            return False, "; ".join(proposed.rejected_reasons)

        from app.bot.dispatcher import bot

        try:
            await bot.send_message(chat_id=telegram_id, text=format_paper_card(proposed.plan), parse_mode="HTML")
        except Exception as e:
            logger.error(f"Failed to send paper trade card to User {user_id}: {e}")
        return True, "opened"

    async def mark_all(self, session, price_source) -> int:
        """Mark every active paper account to market; notify closes. Returns close count."""
        accounts = (
            await session.execute(select(PaperAccount).where(PaperAccount.is_active.is_(True)))
        ).scalars().all()

        symbols: set[str] = set()
        for acc in accounts:
            symbols.update(await get_open_positions(session, acc.id))
        prices: dict[str, float] = {}
        for symbol in symbols:
            try:
                price = await price_source.get_price(symbol)
                if price:
                    prices[symbol] = price
            except Exception as e:
                logger.debug(f"Price fetch failed for {symbol}: {e}")
        if not prices:
            return 0

        from app.bot.dispatcher import bot

        closed_count = 0
        telegram_by_user = {
            u.id: u.telegram_id
            for u in (await session.execute(select(User))).scalars().all()
        }
        for account in accounts:
            events = await self.manager.gateway.mark_account(session, account, prices)
            closed_count += len(events)
            for event in events:
                tg_id = telegram_by_user.get(account.user_id)
                if not tg_id:
                    continue
                try:
                    await bot.send_message(
                        chat_id=tg_id,
                        text=format_close_message(event.symbol, event.reason, event.pnl, event.exit_price),
                        parse_mode="HTML",
                    )
                except Exception as e:
                    logger.error(f"Failed to send paper close alert: {e}")
        await session.commit()
        return closed_count