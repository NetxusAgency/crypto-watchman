"""Execution abstraction.

The rest of the system only ever talks to an `ExecutionGateway`. In this phase
the only implementation is the paper gateway; a live adapter would have to
implement the same interface and would still sit downstream of the risk engine.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from app.services.trading.risk import RiskReport
from app.services.trading.trade_plan import TradePlan


@dataclass
class ExecutionResult:
    ok: bool
    order_id: str = ""
    rejected: list[str] = field(default_factory=list)

    @property
    def rejected_message(self) -> str:
        return "; ".join(self.rejected)


class ExecutionGateway(ABC):
    @abstractmethod
    async def execute(self, plan: TradePlan, risk: RiskReport) -> ExecutionResult:
        raise NotImplementedError

    @abstractmethod
    async def cancel(self, plan_id: str) -> ExecutionResult:
        raise NotImplementedError