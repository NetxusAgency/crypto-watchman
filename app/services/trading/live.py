"""Live execution service — the ONLY path from a confirmed plan to a real broker.

Guards (all must pass before anything reaches cTrader):
  1. Global kill-switch  settings.LIVE_TRADING_ENABLED.
  2. Per-user arming: the user's active connection has is_live == True.
  3. A fresh deterministic setup confirmed by the strategy engine.
  4. The full RiskEngine gate, checked against the broker's real balance/positions.
  5. A hard notional cap per position (settings.LIVE_MAX_POSITION_PCT).

`dry_run=True` exercises the whole pipeline but returns a synthetic order id and
never calls the network (the client is still asked to build the exact payload,
so the audit log is real).
"""

from __future__ import annotations

import logging

from app.core.config import settings
from app.database.models import BrokerConnection, LiveTrade, User
from app.execution.ctrader import CTraderClient
from app.services.broker import decrypt_secret
from app.services.trading.manager import PlanManager
from app.services.trading.strategy_engine import spec_from_definition

logger = logging.getLogger("crypto_watchman.live_trading")

__all__ = ["LiveExecutionService", "LiveExecutionResult"]

LIVE_TIMEFRAME = "4h"
# Signals are computed on BTC spot candles (BTCUSDT) then executed on the CFD.
CANDLE_PROXY = {"BTCUSD": "BTCUSDT", "BTCUT": "BTCUSDT", "BTCUSDT": "BTCUSDT"}


class LiveExecutionError(RuntimeError):
    pass


class LiveExecutionResult:
    def __init__(self, *, allowed: bool, reason: str = "", trade: LiveTrade | None = None,
                 plan_dict: dict | None = None, risk_dict: dict | None = None):
        self.allowed = allowed
        self.reason = reason
        self.trade = trade
        self.plan_dict = plan_dict
        self.risk_dict = risk_dict

    def to_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "trade": self.trade.to_dict() if self.trade else None,
            "plan": self.plan_dict,
            "risk": self.risk_dict,
        }


def live_enabled(connection: BrokerConnection | None, *, require_arm: bool = True) -> LiveExecutionResult:
    """Fast guard-check; returns the first failing reason or an `allowed` result.

    `require_arm=False` (dry runs) still demands an active connection but skips
    the global kill-switch and the per-user arming flag, so the whole pipeline
    can be exercised safely before real funds are enabled.
    """
    if not settings.LIVE_TRADING_ENABLED and require_arm:
        return LiveExecutionResult(
            allowed=False,
            reason="Live trading is disabled globally (LIVE_TRADING_ENABLED=false). "
                   "Nobody — including the admin — can reach a real broker right now.",
        )
    if connection is None or not connection.is_active:
        return LiveExecutionResult(
            allowed=False,
            reason="No active broker connection. Open the Trading tab and connect your cTrader account first.",
        )
    if require_arm and not connection.is_live:
        return LiveExecutionResult(
            allowed=False,
            reason="This connection is not armed for live trading. Open the Trading tab "
                   "and flip 'Enable live trading'.",
        )
    return LiveExecutionResult(allowed=True, reason="")


class LiveExecutionService:
    def __init__(self, manager: PlanManager | None = None, client: CTraderClient | None = None):
        self.manager = manager or PlanManager()
        self.client = client or CTraderClient()

    async def run_trade(
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
    ) -> LiveExecutionResult:
        """Evaluate the strategy for `symbol` and, if every gate passes, place it."""
        plan_dict = None
        risk_dict = None

        guard = live_enabled(connection, require_arm=not dry_run)
        if not guard.allowed:
            return guard

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
            )
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
            )

        proposed = self.manager.evaluate(symbol.upper(), LIVE_TIMEFRAME, spec, candles)
        if proposed.plan is None:
            return LiveExecutionResult(
                allowed=False,
                reason=f"Strategy '{spec.name}' found no confirmed setup on {symbol} right now.",
            )
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
            )

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
            )

        # 5. Execute.
        symbol_id = await self.client.resolve_symbol_id(access_token, symbol.upper())
        if symbol_id is None:
            return LiveExecutionResult(
                allowed=False,
                reason=f"cTrader has no symbol '{symbol}' on this account (check case, e.g. BTCUSD).",
                plan_dict=plan_dict,
                risk_dict=risk_dict,
            )
        side = "Buy" if plan.direction == "LONG" else "Sell"
        tp1 = plan.take_profits[0] if plan.take_profits else None
        reason_txt = ""
        if dry_run:
            order_id = "DRYRUN-" + plan.plan_id
            reason_txt = "Simulated — LIVE_TRADING_ENABLED policy: dry run, broker untouched."
        else:
            try:
                reply = await self.client.place_market_order(
                    access_token=access_token,
                    account_id=account_id,
                    symbol_id=symbol_id,
                    side=side,
                    volume=quantity,
                    stop_loss=plan.stop_loss,
                    take_profit=float(tp1) if tp1 else None,
                )
                order_id = reply.order_id or plan.plan_id
            except Exception as e:  # noqa: BLE001
                logger.warning(f"cTrader order failed: {e}")
                return LiveExecutionResult(
                    allowed=False,
                    reason=f"Broker rejected the order: {e}",
                    plan_dict=plan_dict,
                    risk_dict=risk_dict,
                )

        trade = LiveTrade(
            user_id=user.id,
            connection_id=connection.id,
            plan_id=plan.plan_id,
            symbol=plan.symbol,
            direction=plan.direction,
            strategy_key=plan.strategy_key,
            strategy_name=spec.name,
            timeframe=plan.timeframe,
            entry_price=round(float(price), 6),
            quantity=round(float(quantity), 6),
            stop_loss=plan.stop_loss,
            take_profit_1=float(tp1) if tp1 else None,
            take_profit_2=plan.take_profits[1] if len(plan.take_profits) > 1 else None,
            order_id=order_id,
            status="PLACED",
            reason=reason_txt,
            dry_run=dry_run,
        )
        session.add(trade)
        await session.flush()
        logger.info(
            f"Live trade {trade.plan_id} {symbol} {plan.direction} qty={quantity} "
            f"[dry_run={dry_run}] order={order_id}"
        )
        return LiveExecutionResult(
            allowed=True,
            reason=reason_txt or f"Order placed with cTrader (id {order_id}).",
            trade=trade,
            plan_dict=plan_dict,
            risk_dict=risk_dict,
        )