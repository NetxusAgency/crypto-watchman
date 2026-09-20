"""Plan manager — the state machine that owns a trade's lifecycle.

Stage transitions:
    ANALYSING -> SETUP_FOUND -> RISK_CHECK -> PAPER_PENDING -> PAPER_OPEN
                 -> PAPER_CLOSED     (or -> REJECTED at any gate)
The plan never auto-promotes to live execution; every exit from this module
towards an account is explicitly `PAPER_ONLY`.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.execution.gateway import PaperGateway, get_or_create_paper_account
from app.execution.paper import CloseEvent
from app.services.trading.risk import RiskEngine, RiskReport
from app.services.trading.strategy_engine import StrategySpec, evaluate_signal
from app.services.trading.trade_plan import TradePlan, TradePlanStage, build_trade_plan

__all__ = ["ProposedTrade", "PlanManager"]


@dataclass
class ProposedTrade:
    plan: TradePlan | None
    risk: RiskReport | None
    state: str = "no-setup"  # no-setup / ready / rejected
    stage: TradePlanStage | None = None
    rejected_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "state": self.state,
            "stage": self.stage.value if self.stage else None,
            "plan": self.plan.to_dict() if self.plan else None,
            "risk": self.risk.to_dict() if self.risk else None,
            "rejected_reasons": self.rejected_reasons,
            "paper_only": True,
        }


class PlanManager:
    def __init__(
        self,
        risk: RiskEngine | None = None,
        gateway: PaperGateway | None = None,
    ):
        self.risk = risk or RiskEngine()
        self.gateway = gateway or PaperGateway()

    def evaluate(
        self,
        symbol: str,
        timeframe: str,
        spec: StrategySpec,
        candles: list,
        direction_override: str | None = None,
    ) -> ProposedTrade:
        """Deterministic analysis. No DB, no network. Safe to call in a loop."""
        signal = evaluate_signal(symbol, timeframe, spec, candles, direction_override=direction_override)
        plan = build_trade_plan(signal, spec)
        if plan is None:
            return ProposedTrade(None, None, state="no-setup", stage=TradePlanStage.ANALYSING)
        plan.stage = TradePlanStage.RISK_CHECK
        return ProposedTrade(plan, None, state="setup", stage=plan.stage)

    def assess(self, proposed: ProposedTrade, *, equity: float, open_symbols: list[str] | None = None) -> ProposedTrade:
        """Run the risk gate. Marks the plan REJECTED when any check fails."""
        if proposed.plan is None:
            return proposed
        report = self.risk.assess(
            proposed.plan,
            equity=equity,
            open_symbols=open_symbols,
        )
        if report.passed:
            proposed.plan.stage = TradePlanStage.PAPER_PENDING
            proposed.state = "ready"
        else:
            proposed.plan.stage = TradePlanStage.REJECTED
            proposed.state = "rejected"
            proposed.rejected_reasons = report.blocked_reasons
        proposed.risk = report
        proposed.stage = proposed.plan.stage
        return proposed

    async def commit(
        self,
        session: AsyncSession,
        proposed: ProposedTrade,
        *,
        user_id: int | None = None,
    ) -> ProposedTrade:
        """Execute an approved plan against the paper gateway (PAPER_ONLY)."""
        if proposed.plan is None or proposed.state != "ready" or proposed.risk is None:
            return proposed
        if user_id is not None:
            proposed.plan.context["user_id"] = user_id
        result = await self.gateway.execute(session, proposed.plan, proposed.risk)
        if result.ok:
            proposed.plan.stage = TradePlanStage.PAPER_OPEN
            proposed.stage = proposed.plan.stage
            proposed.state = "open"
        else:
            proposed.plan.stage = TradePlanStage.REJECTED
            proposed.stage = proposed.plan.stage
            proposed.state = "rejected"
            proposed.rejected_reasons = result.rejected
        return proposed

    async def monitor(
        self,
        session: AsyncSession,
        user_id: int,
        prices: dict[str, float],
    ) -> tuple[list[CloseEvent], dict]:
        """Mark the paper account to market and return close events."""
        account = await get_or_create_paper_account(session, user_id)
        events = await self.gateway.mark_account(session, account, prices)
        stats = self.gateway.engine.stats(events)
        return events, {"cash": account.cash, "equity": account.equity, "stats": stats}