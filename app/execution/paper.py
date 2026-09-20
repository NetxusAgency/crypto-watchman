"""Paper trading engine.

Pure Python P&L logic that the DB persistence layer and the trade monitor both
sit on. Everything here is deterministic and unit-testable without a database.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.services.trading.risk import RiskEngine, RiskReport
from app.services.trading.trade_plan import TradePlan, TradePlanStage

__all__ = [
    "CloseEvent",
    "PaperPositionState",
    "PaperState",
    "PaperExecutionEngine",
]

DEFAULT_INITIAL_BALANCE = 10_000.0
POLICY_FEE_BPS = 5.0  # 0.05% per side, like a maker fee


@dataclass
class CloseEvent:
    symbol: str
    plan_id: str
    reason: str  # STOP / TP1 / TP2 / INVALIDATION / CANCEL
    entry_price: float
    exit_price: float
    quantity: float
    direction: str
    pnl: float
    fee: float


@dataclass
class PaperPositionState:
    plan_id: str
    symbol: str
    direction: str  # LONG / SHORT
    strategy_key: str
    timeframe: str
    entry_price: float
    quantity: float
    stop_loss: float | None
    take_profit_1: float | None
    take_profit_2: float | None
    invalidation: float | None = None
    status: str = "OPEN"

    def unrealised_pnl(self, price: float) -> float:
        if self.direction == "LONG":
            return (price - self.entry_price) * self.quantity
        return (self.entry_price - price) * self.quantity

    def unrealised_pct(self, price: float) -> float:
        base = self.entry_price * self.quantity or 1.0
        return self.unrealised_pnl(price) / base * 100.0


@dataclass
class PaperState:
    cash: float
    positions: dict[str, PaperPositionState]  # symbol -> position
    initial_balance: float = DEFAULT_INITIAL_BALANCE

    @property
    def equity(self) -> float:
        return self.cash

    @property
    def open_count(self) -> int:
        return len(self.positions)


class PaperExecutionEngine:
    """Deterministic paper match + mark-to-market. No randomness, no DB."""

    def __init__(
        self,
        initial_balance: float = DEFAULT_INITIAL_BALANCE,
        fee_bps: float = POLICY_FEE_BPS,
        risk_engine: RiskEngine | None = None,
    ):
        self.initial_balance = initial_balance
        self.fee_bps = fee_bps
        self.risk = risk_engine or RiskEngine()

    def fresh_state(self) -> PaperState:
        return PaperState(cash=self.initial_balance, positions={}, initial_balance=self.initial_balance)

    def _fee(self, notional: float) -> float:
        return notional * (self.fee_bps / 10_000.0)

    def should_enter_state(self, plan: TradePlan) -> bool:
        """A openable order needs a full risk-approval and a sane stop."""
        return (
            plan.stage in (TradePlanStage.SETUP_FOUND, TradePlanStage.PAPER_PENDING)
            and plan.direction in ("LONG", "SHORT")
            and bool(plan.stop_loss and plan.stop_loss > 0)
        )

    def open_position(
        self,
        state: PaperState,
        plan: TradePlan,
        market_price: float | None = None,
        risk_report: RiskReport | None = None,
    ) -> tuple[PaperPositionState | None, list[str]]:
        """Open a position when the market is inside the plan's entry zone.

        Returns (fill, rejection_reasons). Rejects if the plan is not
        ready/valid or the RiskEngine gate does not pass.
        """
        rejected: list[str] = []
        if market_price is None:
            market_price = float(plan.context.get("snapshot", {}).get("current_price") or plan.entry_high)
        if not self.should_enter_state(plan):
            rejected.append("plan-not-approved")
        if market_price < plan.entry_low or market_price > plan.entry_high:
            rejected.append(f"price-outside-entry-zone ({market_price} vs {plan.entry_low}-{plan.entry_high})")
        if state.open_count and plan.symbol in state.positions:
            rejected.append("duplicate-position")
        if risk_report is not None and not risk_report.passed:
            rejected.extend(risk_report.blocked_reasons)
        if rejected:
            return None, rejected

        price = market_price or plan.entry_high
        quantity = self._quantity(plan, state.cash)
        fee = self._fee(price * quantity)
        position = PaperPositionState(
            plan_id=plan.plan_id,
            symbol=plan.symbol,
            direction=plan.direction,
            strategy_key=plan.strategy_key,
            timeframe=plan.timeframe,
            entry_price=price,
            quantity=quantity,
            stop_loss=plan.stop_loss,
            take_profit_1=plan.take_profits[0] if plan.take_profits else None,
            take_profit_2=plan.take_profits[1] if len(plan.take_profits) > 1 else None,
        )
        state.positions[plan.symbol] = position
        state.cash -= fee
        return position, []

    def _quantity(self, plan: TradePlan, cash: float) -> float:
        size = self.risk.position_size(plan, equity=cash)
        if size is None or size <= 0:
            return 1.0
        price = plan.entry_high or 1.0
        cap = (cash * 0.95) / price  # never deploy more than 95% of cash
        return round(min(size, cap), 8)

    def mark(self, state: PaperState, prices: dict[str, float]) -> list[CloseEvent]:
        """Close everything whose SL/TP (or invalidation) has been hit."""
        events: list[CloseEvent] = []
        for symbol, pos in list(state.positions.items()):
            price = prices.get(symbol)
            if price is None:
                continue
            hit = self._trigger(pos, price)
            if hit is None:
                continue
            exit_price, reason = hit
            gross = (
                (exit_price - pos.entry_price) * pos.quantity
                if pos.direction == "LONG"
                else (pos.entry_price - exit_price) * pos.quantity
            )
            fee = self._fee(exit_price * pos.quantity)
            pnl = gross - fee
            state.cash += pnl
            events.append(CloseEvent(
                symbol=symbol,
                plan_id=pos.plan_id,
                reason=reason,
                entry_price=pos.entry_price,
                exit_price=exit_price,
                quantity=pos.quantity,
                direction=pos.direction,
                pnl=gross,
                fee=fee,
            ))
            del state.positions[symbol]
        return events

    def _trigger(self, pos: PaperPositionState, price: float) -> tuple[float, str] | None:
        sl = pos.stop_loss
        tp = pos.take_profit_1
        if pos.invalidation is not None:
            inv = pos.invalidation
            if pos.direction == "LONG" and price <= inv:
                return inv, "INVALIDATION"
            if pos.direction == "SHORT" and price >= inv:
                return inv, "INVALIDATION"
        if pos.direction == "LONG":
            if sl and price <= sl:
                return sl, "STOP"
            if tp and price >= tp:
                return tp, "TP1"
            if pos.take_profit_2 and price >= pos.take_profit_2:
                return pos.take_profit_2, "TP2"
        else:
            if sl and price >= sl:
                return sl, "STOP"
            if tp and price <= tp:
                return tp, "TP1"
            if pos.take_profit_2 and price <= pos.take_profit_2:
                return pos.take_profit_2, "TP2"
        return None

    def stats(self, events: list[CloseEvent]) -> dict:
        if not events:
            return {"closed": 0, "win_rate": 0.0, "profit_factor": None, "net_pnl": 0.0}
        wins = [e.pnl for e in events if e.pnl > 0]
        losses = [e.pnl for e in events if e.pnl <= 0]
        gross_win = sum(wins)
        gross_loss = abs(sum(losses))
        return {
            "closed": len(events),
            "win_rate": round(len(wins) / len(events) * 100, 2),
            "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else None,
            "net_pnl": round(sum(e.pnl for e in events), 2),
        }