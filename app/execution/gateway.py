"""Persistence-backed paper gateway.

Maps the pure `PaperExecutionEngine` onto the paper DB tables. All writes go
through one session; the monitor calls `mark_account` with a price map.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from app.database.models import PaperAccount, PaperFill, PaperPosition
from app.execution.base import ExecutionGateway, ExecutionResult
from app.execution.paper import CloseEvent, PaperExecutionEngine, PaperPositionState
from app.services.trading.risk import RiskReport
from app.services.trading.trade_plan import TradePlan


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def get_or_create_paper_account(session, user_id: int) -> PaperAccount:
    result = await session.execute(
        select(PaperAccount).where(PaperAccount.user_id == user_id, PaperAccount.is_active.is_(True))
    )
    account = result.scalar_one_or_none()
    if account is None:
        account = PaperAccount(user_id=user_id)
        session.add(account)
        await session.flush()
    return account


def _position_state_from_row(row: PaperPosition) -> PaperPositionState:
    return PaperPositionState(
        plan_id=row.plan_id,
        symbol=row.symbol,
        direction=row.direction,
        strategy_key=row.strategy_key,
        timeframe=row.timeframe,
        entry_price=row.entry_price,
        quantity=row.quantity,
        stop_loss=row.stop_loss,
        take_profit_1=row.take_profit_1,
        take_profit_2=row.take_profit_2,
    )


class PaperGateway(ExecutionGateway):
    def __init__(self, engine: PaperExecutionEngine | None = None):
        self.engine = engine or PaperExecutionEngine()

    async def execute(self, session, plan: TradePlan, risk: RiskReport) -> ExecutionResult:
        if not risk.passed:
            return ExecutionResult(ok=False, rejected=risk.blocked_reasons)

        user_id = plan.context.get("user_id") or 1
        account = await get_or_create_paper_account(session, user_id=user_id)
        state = self.engine.fresh_state()
        state.cash = account.cash
        for row in await self._open_rows(session, account.id):
            state.positions[row.symbol] = _position_state_from_row(row)

        market_price = float(plan.context.get("snapshot", {}).get("current_price") or plan.entry_high)
        fill, rejected = self.engine.open_position(state, plan, market_price=market_price, risk_report=risk)
        if fill is None:
            return ExecutionResult(ok=False, rejected=rejected)

        await self._persist_open(session, account, plan, fill)
        account.cash = state.cash
        return ExecutionResult(ok=True, order_id=fill.plan_id)

    async def _open_rows(self, session, account_id: int):
        result = await session.execute(
            select(PaperPosition).where(PaperPosition.account_id == account_id, PaperPosition.status == "OPEN")
        )
        return result.scalars().all()

    async def _persist_open(self, session, account: PaperAccount, plan: TradePlan, fill) -> None:
        row = PaperPosition(
            account_id=account.id,
            plan_id=fill.plan_id,
            symbol=fill.symbol,
            strategy_key=plan.strategy_key,
            timeframe=plan.timeframe,
            direction=fill.direction,
            status="OPEN",
            entry_price=fill.entry_price,
            quantity=fill.quantity,
            stop_loss=plan.stop_loss,
            take_profit_1=plan.take_profits[0] if plan.take_profits else None,
            take_profit_2=plan.take_profits[1] if len(plan.take_profits) > 1 else None,
        )
        session.add(row)
        await session.flush()
        session.add(PaperFill(position_id=row.id, side="BUY" if fill.direction == "LONG" else "SELL", price=fill.entry_price, quantity=fill.quantity))

    async def mark_account(self, session, account: PaperAccount, prices: dict[str, float]) -> list[CloseEvent]:
        rows = await self._open_rows(session, account.id)
        state = self.engine.fresh_state()
        state.cash = account.cash
        for row in rows:
            state.positions[row.symbol] = _position_state_from_row(row)
        events = self.engine.mark(state, prices)
        for event in events:
            row = next((r for r in rows if r.plan_id == event.plan_id), None)
            if row is None:
                continue
            row.status = "CLOSED"
            row.close_reason = event.reason
            row.exit_price = event.exit_price
            row.realized_pnl = event.pnl
            row.closed_at = _utcnow()
            session.add(PaperFill(position_id=row.id, side="SELL" if event.direction == "LONG" else "BUY", price=event.exit_price, quantity=event.quantity))
        account.cash = state.cash
        return events