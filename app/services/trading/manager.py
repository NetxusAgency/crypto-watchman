"""Plan manager — the state machine that owns a trade's lifecycle.

Stage transitions:
    ANALYSING -> SETUP_FOUND -> RISK_CHECK -> PENDING_CONFIRM
                 -> PLACED -> CLOSED     (or -> REJECTED at any gate)
The manager is broker-agnostic: it analyses signals and runs the risk gate.
Execution against a cTrader account happens exclusively through
`LiveExecutionService` in `app/services/trading/live.py`.
"""
from __future__ import annotations

from dataclasses import dataclass, field

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
            "verified_for_live": True,
        }


class PlanManager:
    def __init__(self, risk: RiskEngine | None = None):
        self.risk = risk or RiskEngine()

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
            proposed.plan.stage = TradePlanStage.PENDING_CONFIRM
            proposed.state = "ready"
        else:
            proposed.plan.stage = TradePlanStage.REJECTED
            proposed.state = "rejected"
            proposed.rejected_reasons = report.blocked_reasons
        proposed.risk = report
        proposed.stage = proposed.plan.stage
        return proposed