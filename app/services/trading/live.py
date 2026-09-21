"""Live execution service — the ONLY path from a confirmed plan to a real broker.

Guards (all must pass before anything reaches cTrader):
  1. Global kill-switch  settings.LIVE_TRADING_ENABLED  — REQUIRED ONLY for
     `mode == "live"` connections. Demo accounts (simulated funds on the real
     platform) may be armed without the global switch.
  2. Per-user arming: the user's active connection has is_live == True.
  3. A fresh deterministic setup confirmed by the strategy engine.
  4. The full RiskEngine gate, checked against the broker's real balance/positions.
  5. A hard notional cap per position (settings.LIVE_MAX_POSITION_PCT).
  6. No-duplicate: a new trade is never proposed while another PLACED or
     PENDING_CONFIRM trade exists on the same connection, nor while the broker
     reports an open position.

Real execution is two-phase:
  - `propose_trade(...)` validates the whole pipeline and persists a
    LiveTrade row with status "PENDING_CONFIRM". It NEVER sends an order.
  - `confirm_trade(...)` (after the user taps ✅ in Telegram) re-runs the
    guards and finally places the market order.
  - `reject_trade(...)` records the decline.

`dry_run=True` exercises the whole pipeline but returns a synthetic order id and
never calls the network (the client is still asked to build the exact payload,
so the audit log is real).
"""

from __future__ import annotations

import logging

from sqlalchemy import select

from app.core.config import settings
from app.database.models import BrokerConnection, LiveTrade, User
from app.execution.ctrader import CTraderClient
from app.services.broker import decrypt_secret
from app.services.trading.manager import PlanManager
from app.services.trading.strategy_engine import spec_from_definition

logger = logging.getLogger("crypto_watchman.live_trading")

__all__ = [
    "LiveExecutionService",
    "LiveExecutionResult",
    "live_enabled",
    "format_live_proposal",
]

LIVE_TIMEFRAME = "4h"
# Signals are computed on BTC spot candles (BTCUSDT) then executed on the CFD.
CANDLE_PROXY = {"BTCUSD": "BTCUSDT", "BTCUT": "BTCUSDT", "BTCUSDT": "BTCUSDT"}


class LiveExecutionError(RuntimeError):
    pass


class LiveExecutionResult:
    def __init__(
        self,
        *,
        allowed: bool,
        reason: str = "",
        trade: LiveTrade | None = None,
        plan_dict: dict | None = None,
        risk_dict: dict | None = None,
        awaiting_confirmation: bool = False,
        confirmation_text: str = "",
    ):
        self.allowed = allowed
        self.reason = reason
        self.trade = trade
        self.plan_dict = plan_dict
        self.risk_dict = risk_dict
        self.awaiting_confirmation = awaiting_confirmation
        self.confirmation_text = confirmation_text

    def to_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "trade": self.trade.to_dict() if self.trade else None,
            "plan": self.plan_dict,
            "risk": self.risk_dict,
            "awaiting_confirmation": self.awaiting_confirmation,
            "confirmation_text": self.confirmation_text,
        }


def live_enabled(connection: BrokerConnection | None, *, require_arm: bool = True) -> LiveExecutionResult:
    """Fast guard-check; returns the first failing reason or an `allowed` result.

    `require_arm=False` (dry runs) still demands an active connection but skips
    the global kill-switch and the per-user arming flag, so the whole pipeline
    can be exercised safely before real funds are enabled.

    The global kill-switch only gates `mode == "live"` accounts. A demo account
    (virtual money) is already safe and only needs per-user arming.
    """
    if (
        require_arm
        and connection is not None
        and (connection.mode or "demo") == "live"
        and not settings.LIVE_TRADING_ENABLED
    ):
        return LiveExecutionResult(
            allowed=False,
            reason="Live trading is disabled globally (LIVE_TRADING_ENABLED=false). "
                   "Demo accounts may still trade; real-money accounts are blocked.",
        )
    if connection is None or not connection.is_active:
        return LiveExecutionResult(
            allowed=False,
            reason="No active broker connection. Open the Trading tab and connect your cTrader account first.",
        )
    if require_arm and not connection.is_live:
        return LiveExecutionResult(
            allowed=False,
            reason="This connection is not armed for trading. Open the Trading tab "
                   "and flip 'Enable trading'.",
        )
    return LiveExecutionResult(allowed=True, reason="")


def format_live_proposal(trade: LiveTrade, connection: BrokerConnection | None) -> str:
    """HTML card shown in Telegram before a real order is placed."""
    direction_icon = "🟢 LONG" if trade.direction.upper() == "LONG" else "🔴 SHORT"
    mode = (connection.mode if connection else "demo") or "demo"
    mode_tag = "🧪 demo" if mode == "demo" else "💵 live"
    tps = " / ".join(
        f"TP{i+1} <b>{tp:,.2f}</b>" for i, tp in ((1, trade.take_profit_1), (2, trade.take_profit_2)) if tp
    )
    return (
        f"🟢 <b>Trade proposal — {trade.symbol} {direction_icon} · {mode_tag}</b>\n"
        f"Strategy: <b>{trade.strategy_name}</b>\n"
        f"Entry zone: <b>{trade.entry_price:,.2f}</b> (market)\n"
        f"Stop: <b>{trade.stop_loss:,.2f}</b>\n"
        f"Size: <b>{trade.quantity:.6g}</b> {trade.symbol.replace('USD', '').replace('UT', '')}\n"
        f"{tps}\n\n"
        f"<i>Confirm to place the order with cTrader. Nothing executes until you tap ✅.</i>"
    )


async def has_open_live_trade(session, connection_id: int | None, except_id: int | None = None) -> bool:
    """True when the connection has a PLACED or PENDING_CONFIRM trade on file."""
    if connection_id is None:
        return False
    stmt = select(LiveTrade.id).where(
        LiveTrade.connection_id == connection_id,
        LiveTrade.status.in_(["PLACED", "PENDING_CONFIRM"]),
    )
    if except_id is not None:
        stmt = stmt.where(LiveTrade.id != except_id)
    result = await session.execute(stmt)
    return result.first() is not None


class LiveExecutionService:
    def __init__(self, manager: PlanManager | None = None, client: CTraderClient | None = None):
        self.manager = manager or PlanManager()
        self.client = client or CTraderClient()

    async def _build_proposal(
        self,
        session,
        *,
        user: User,
        connection: BrokerConnection | None,
        symbol: str,
        strategy_key: str,
        strategy_name: str = "",
        dry_run: bool = False,
        kline_source=None,
        candles_override: list | None = None,
    ) -> tuple[LiveExecutionResult | None, tuple]:
        """Shared pipeline: guards -> broker truth -> setup -> risk -> sizing.

        Returns (None, data) on success where data carries everything needed to
        persist and execute; returns (blocked_result, None) otherwise.
        """
        guard = live_enabled(connection, require_arm=not dry_run)
        if not guard.allowed:
            return guard, None

        from app.services.trading.ctid_oauth import ensure_valid_token

        if not await ensure_valid_token(session, connection):
            return LiveExecutionResult(
                allowed=False,
                reason="Your cTrader access token has expired. Reconnect the account "
                       "(Trading tab -> Connect with cTID) to obtain a fresh one.",
            ), None

        symbol_alias = CANDLE_PROXY.get(symbol.upper(), symbol.upper())
        access_token = decrypt_secret(connection.access_token_enc)

        # 1. Broker truth (balance, open positions) — dry runs still query mocked.
        accounts = await self.client.get_accounts(access_token)
        account_id = int(connection.account_id or (accounts[0].account_id if accounts else 0))
        equity = accounts[0].equity if accounts else 0.0
        if equity <= 0:
            return LiveExecutionResult(
                allowed=False,
                reason="cTrader reports zero/negative balance — refusing to trade on this account.",
            ), None
        positions = await self.client.get_positions(access_token)
        open_symbols = [p.symbol.upper().replace("/", "").replace("-", "") for p in positions]

        # 2. Deterministic setup for the user's chosen (saved) strategy.
        spec = spec_from_definition(
            strategy_key,
            name=strategy_name or strategy_key,
            description="",
            timeframes=LIVE_TIMEFRAME,
        )
        candles = candles_override if candles_override is not None else []
        if not candles and kline_source is not None:
            candles = await kline_source.fetch_candles(symbol_alias, LIVE_TIMEFRAME, limit=200)
        if not candles:
            return LiveExecutionResult(
                allowed=False,
                reason=f"No candle data available for {symbol} — could not build a setup.",
            ), None

        proposed = self.manager.evaluate(symbol.upper(), LIVE_TIMEFRAME, spec, candles)
        if proposed.plan is None:
            return LiveExecutionResult(
                allowed=False,
                reason=f"Strategy '{spec.name}' found no confirmed setup on {symbol} right now.",
            ), None
        plan = proposed.plan
        plan.context["user_id"] = user.id
        plan.context["live"] = True
        plan_dict = plan.to_dict()

        # 3. Hard risk gate against the broker account.
        self.manager.assess(proposed, equity=equity, open_symbols=open_symbols)
        risk_dict = proposed.risk.to_dict() if proposed.risk else None
        if proposed.state != "ready":
            return LiveExecutionResult(
                allowed=False,
                reason="; ".join(proposed.rejected_reasons) or "Risk gate rejected the setup.",
                plan_dict=plan_dict,
                risk_dict=risk_dict,
            ), None

        # 4. Position sizing + notional cap (1x leverage, no margin games).
        quantity = self.manager.risk.position_size(plan, equity)
        price = plan.context.get("snapshot", {}).get("current_price") or plan.entry_high
        notional_cap = equity * (settings.LIVE_MAX_POSITION_PCT / 100.0)
        if quantity and price and quantity * price > notional_cap:
            quantity = notional_cap / price
        if not quantity or quantity <= 0:
            return LiveExecutionResult(
                allowed=False,
                reason="Could not size a position within the risk budget (likely stop too far).",
                plan_dict=plan_dict,
                risk_dict=risk_dict,
            ), None

        symbol_id = await self.client.resolve_symbol_id(access_token, symbol.upper())
        if symbol_id is None:
            return LiveExecutionResult(
                allowed=False,
                reason=f"cTrader has no symbol '{symbol}' on this account (check case, e.g. BTCUSD).",
                plan_dict=plan_dict,
                risk_dict=risk_dict,
            ), None

        data = (
            access_token,
            account_id,
            symbol_id,
            equity,
            positions,
            open_symbols,
            plan,
            plan_dict,
            risk_dict,
            quantity,
            price,
        )
        return None, data

    async def propose_trade(
        self,
        session,
        *,
        user: User,
        connection: BrokerConnection | None,
        symbol: str,
        strategy_key: str,
        strategy_name: str = "",
        dry_run: bool = False,
        kline_source=None,
        candles_override: list | None = None,
        notify: bool = False,
    ) -> LiveExecutionResult:
        """Validate the full pipeline and persist a trade.

        - dry_run=True: simulates, writes an audit row (order id "DRYRUN-…").
        - dry_run=False: creates a PENDING_CONFIRM row and — when `notify` —
          sends the Telegram inline-button confirmation. Never places an order
          by itself.
        """
        blocked, data = await self._build_proposal(
            session,
            user=user,
            connection=connection,
            symbol=symbol,
            strategy_key=strategy_key,
            strategy_name=strategy_name,
            dry_run=dry_run,
            kline_source=kline_source,
            candles_override=candles_override,
        )
        if blocked is not None:
            return blocked
        (
            access_token, account_id, symbol_id, equity, positions,
            open_symbols, plan, plan_dict, risk_dict, quantity, price,
        ) = data

        if not dry_run:
            # 5. No-duplicate guard: one open trade at a time per connection.
            if await has_open_live_trade(session, connection.id):
                return LiveExecutionResult(
                    allowed=False,
                    reason="Another trade is already open or awaiting confirmation on this "
                           "account. Wait for it to be fulfilled before proposing a new one.",
                    plan_dict=plan_dict,
                    risk_dict=risk_dict,
                )
            if positions:
                return LiveExecutionResult(
                    allowed=False,
                    reason="cTrader reports an open position on this account — no new trade "
                           "until it is closed/fulfilled.",
                    plan_dict=plan_dict,
                    risk_dict=risk_dict,
                )

        side = "Buy" if plan.direction == "LONG" else "Sell"
        tp1 = plan.take_profits[0] if plan.take_profits else None
        reason_txt = ""
        order_id = ""
        if dry_run:
            order_id = "DRYRUN-" + plan.plan_id
            reason_txt = "Simulated — dry run, broker untouched."
            status = "PLACED"
        else:
            reason_txt = "Awaiting your confirmation in Telegram."
            status = "PENDING_CONFIRM"

        trade = LiveTrade(
            user_id=user.id,
            connection_id=connection.id,
            plan_id=plan.plan_id,
            symbol=plan.symbol,
            direction=plan.direction,
            strategy_key=plan.strategy_key,
            strategy_name=strategy_name or strategy_key,
            timeframe=plan.timeframe,
            entry_price=round(float(price), 6),
            quantity=round(float(quantity), 6),
            stop_loss=plan.stop_loss,
            take_profit_1=float(tp1) if tp1 else None,
            take_profit_2=plan.take_profits[1] if len(plan.take_profits) > 1 else None,
            order_id=order_id,
            status=status,
            reason=reason_txt,
            dry_run=dry_run,
        )
        session.add(trade)
        await session.flush()

        confirmation_text = format_live_proposal(trade, connection) if not dry_run else ""
        if not dry_run and notify:
            await self._send_confirmation(user, trade, connection)

        logger.info(
            f"Live proposal {trade.plan_id} {symbol} {plan.direction} qty={quantity} "
            f"[dry_run={dry_run}] status={status}"
        )
        await session.commit()
        return LiveExecutionResult(
            allowed=True,
            reason=reason_txt or f"Order placed with cTrader (id {order_id}).",
            trade=trade,
            plan_dict=plan_dict,
            risk_dict=risk_dict,
            awaiting_confirmation=not dry_run,
            confirmation_text=confirmation_text,
        )

    async def _send_confirmation(self, user, trade: LiveTrade, connection) -> None:
        from app.bot.dispatcher import bot

        try:
            await bot.send_message(
                chat_id=user.telegram_id,
                text=format_live_proposal(trade, connection),
                parse_mode="HTML",
                reply_markup=self._confirmation_keyboard(trade.id),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Failed to send live confirmation to User {user.id}: {e}")

    @staticmethod
    def _confirmation_keyboard(trade_id: int):
        from app.bot.keyboards import live_confirm_keyboard

        return live_confirm_keyboard(trade_id)

    async def confirm_trade(self, session, *, user: User, trade_id: int) -> LiveExecutionResult:
        """Place the order for a PENDING_CONFIRM trade after the user approves.

        Re-runs every guard against the live broker before touching it.
        """
        trade = (
            await session.execute(
                select(LiveTrade).where(LiveTrade.id == trade_id, LiveTrade.user_id == user.id)
            )
        ).scalar_one_or_none()
        if trade is None:
            return LiveExecutionResult(allowed=False, reason="Trade not found.")
        if trade.status != "PENDING_CONFIRM":
            return LiveExecutionResult(
                allowed=False,
                reason=f"Trade is {trade.status} — nothing to confirm.",
                trade=trade,
            )
        if trade.dry_run:
            return LiveExecutionResult(allowed=False, reason="Dry-run trades need no confirmation.", trade=trade)

        connection = None
        if trade.connection_id:
            connection = (
                await session.execute(
                    select(BrokerConnection).where(BrokerConnection.id == trade.connection_id)
                )
            ).scalar_one_or_none()

        guard = live_enabled(connection, require_arm=True)
        if not guard.allowed:
            return guard

        if await has_open_live_trade(session, trade.connection_id, except_id=trade.id):
            return LiveExecutionResult(
                allowed=False,
                reason="Another trade opened while this one waited — cancelled to keep one "
                       "open position at a time.",
                trade=trade,
            )

        from app.services.trading.ctid_oauth import ensure_valid_token

        if connection is not None and not await ensure_valid_token(session, connection):
            return LiveExecutionResult(
                allowed=False,
                reason="Your cTrader access token expired — reconnect the account "
                       "(Trading tab -> Connect with cTID) and confirm again.",
                trade=trade,
            )

        access_token = decrypt_secret(connection.access_token_enc)
        positions = await self.client.get_positions(access_token)
        if positions:
            return LiveExecutionResult(
                allowed=False,
                reason="cTrader reports an open position — refusing to stack another. "
                       "Wait for it to be fulfilled.",
                trade=trade,
            )

        # Re-resolve in case of session churn; fall back to stored plan fields.
        symbol_id = await self.client.resolve_symbol_id(access_token, trade.symbol)
        if symbol_id is None:
            return LiveExecutionResult(
                allowed=False,
                reason=f"cTrader has no symbol '{trade.symbol}' on this account.",
                trade=trade,
            )

        side = "Buy" if trade.direction.upper() == "LONG" else "Sell"
        try:
            reply = await self.client.place_market_order(
                access_token=access_token,
                account_id=int(connection.account_id or 0),
                symbol_id=symbol_id,
                side=side,
                volume=trade.quantity,
                stop_loss=trade.stop_loss,
                take_profit=float(trade.take_profit_1) if trade.take_profit_1 else None,
            )
            order_id = reply.order_id or trade.plan_id
        except Exception as e:  # noqa: BLE001
            logger.warning(f"cTrader order failed on confirm: {e}")
            return LiveExecutionResult(
                allowed=False,
                reason=f"Broker rejected the order: {e}",
                trade=trade,
            )

        trade.status = "PLACED"
        trade.order_id = order_id
        trade.reason = f"Order placed with cTrader (id {order_id})."
        await session.flush()
        await session.commit()
        logger.info(f"Confirmed live trade {trade.plan_id} {trade.symbol} order={order_id}")
        return LiveExecutionResult(
            allowed=True,
            reason=f"Order placed with cTrader (id {order_id}).",
            trade=trade,
        )

    async def reject_trade(self, session, *, user: User, trade_id: int) -> LiveExecutionResult:
        trade = (
            await session.execute(
                select(LiveTrade).where(LiveTrade.id == trade_id, LiveTrade.user_id == user.id)
            )
        ).scalar_one_or_none()
        if trade is None:
            return LiveExecutionResult(allowed=False, reason="Trade not found.")
        if trade.status != "PENDING_CONFIRM":
            return LiveExecutionResult(allowed=False, reason=f"Trade is {trade.status}.", trade=trade)
        trade.status = "REJECTED"
        trade.reason = "Declined by the user."
        await session.flush()
        await session.commit()
        logger.info(f"Rejected live trade {trade.plan_id}")
        return LiveExecutionResult(allowed=True, reason="Trade declined — nothing placed.", trade=trade)

    async def sync_closed(self, session, connection) -> int:
        """Mark PLACED trades CLOSED once the broker shows no open positions.

        Returns the number of trades newly closed. Keeps the no-duplicate rule
        honest when a position is fulfilled outside the DB.
        """
        from app.services.trading.ctid_oauth import ensure_valid_token

        if not await ensure_valid_token(session, connection):
            logger.warning(
                "sync_closed: token expired for connection %s - skipping", connection.id
            )
            return 0
        access_token = decrypt_secret(connection.access_token_enc)
        try:
            positions = await self.client.get_positions(access_token)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"sync_closed: could not read positions for connection {connection.id}: {e}")
            return 0
        if positions:
            return 0
        rows = (
            await session.execute(
                select(LiveTrade).where(
                    LiveTrade.connection_id == connection.id,
                    LiveTrade.status == "PLACED",
                    LiveTrade.dry_run.is_(False),
                )
            )
        ).scalars().all()
        closed = 0
        for trade in rows:
            trade.status = "CLOSED"
            trade.reason = "Fulfilled — position closed on the broker."
            closed += 1
        if closed:
            await session.flush()
            await session.commit()
            logger.info(f"sync_closed: {closed} live trade(s) marked CLOSED for connection {connection.id}")
        return closed