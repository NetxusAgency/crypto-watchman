"""Trade plans: the machine-readable output of a confirmed trade signal.

A TradePlan is the single carrier handed from the strategy engine to the risk
engine and finally the live execution service. Plans are broker-agnostic; they
describe entries, stops and targets but never touch an account themselves.
"""

import random
import string
from datetime import datetime, timezone
from enum import Enum

from app.services.trading.strategy_engine import StrategySignal, StrategySpec

__all__ = ["TradePlan", "TradePlanStage", "build_trade_plan", "new_plan_id"]

STAGES = (
    "ANALYSING",
    "SETUP_FOUND",
    "RISK_CHECK",
    "PENDING_CONFIRM",
    "PLACED",
    "CLOSED",
    "REJECTED",
)


class TradePlanStage(str, Enum):
    ANALYSING = "ANALYSING"
    SETUP_FOUND = "SETUP_FOUND"
    RISK_CHECK = "RISK_CHECK"
    PENDING_CONFIRM = "PENDING_CONFIRM"
    PLACED = "PLACED"
    CLOSED = "CLOSED"
    REJECTED = "REJECTED"


def new_plan_id() -> str:
    alphabet = string.ascii_uppercase + string.digits
    stamp = datetime.now(timezone.utc).strftime("%y%m%d")
    suffix = "".join(random.SystemRandom().choice(alphabet) for _ in range(6))
    return f"TP-{stamp}-{suffix}"


class TradePlan:
    __slots__ = (
        "plan_id", "symbol", "direction", "timeframe", "strategy_key", "strategy_name",
        "entry_type", "entry_low", "entry_high", "stop_loss", "take_profits",
        "invalidation", "risk_reward", "setup_status", "stage", "confidence",
        "signal_reason", "rule_results", "context", "created_at",
    )

    def __init__(
        self,
        *,
        symbol: str,
        direction: str,
        timeframe: str,
        strategy_key: str,
        strategy_name: str,
        entry_type: str = "LIMIT",
        entry_low: float,
        entry_high: float,
        stop_loss: float,
        take_profits: list[float],
        invalidation: str,
        risk_reward: float,
        setup_status: str = "VALID",
        stage: TradePlanStage = TradePlanStage.SETUP_FOUND,
        confidence: int = 50,
        signal_reason: list[str] | None = None,
        rule_results: list | None = None,
        context: dict | None = None,
        plan_id: str | None = None,
    ):
        self.plan_id = plan_id or new_plan_id()
        self.symbol = symbol.upper()
        self.direction = direction.upper()
        self.timeframe = timeframe
        self.strategy_key = strategy_key
        self.strategy_name = strategy_name
        self.entry_type = entry_type
        self.entry_low = entry_low
        self.entry_high = entry_high
        self.stop_loss = stop_loss
        self.take_profits = take_profits
        self.invalidation = invalidation
        self.risk_reward = risk_reward
        self.setup_status = setup_status
        self.stage = stage
        self.confidence = confidence
        self.signal_reason = list(signal_reason or [])
        self.rule_results = list(rule_results or [])
        self.context = context or {}
        self.created_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return {
            "plan_id": self.plan_id,
            "symbol": self.symbol,
            "direction": self.direction,
            "timeframe": self.timeframe,
            "strategy_key": self.strategy_key,
            "strategy_name": self.strategy_name,
            "entry_type": self.entry_type,
            "entry_low": self.entry_low,
            "entry_high": self.entry_high,
            "stop_loss": self.stop_loss,
            "take_profits": self.take_profits,
            "invalidation": self.invalidation,
            "risk_reward": self.risk_reward,
            "setup_status": self.setup_status,
            "stage": self.stage.value if isinstance(self.stage, TradePlanStage) else self.stage,
            "confidence": self.confidence,
            "signal_reason": self.signal_reason,
            "rule_results": [r.to_dict() if hasattr(r, "to_dict") else r for r in self.rule_results],
            "context": self.context,
            "created_at": self.created_at,
            "verified_for_live": True,
        }


def _round4(v: float) -> float:
    return round(float(v), 4)


def build_trade_plan(signal: StrategySignal, spec: StrategySpec | None = None) -> TradePlan | None:
    """Deterministically derive entry/SL/TP levels from a confirmed signal.

    Returns None when the signal is not confirmed, so the caller can report
    "no setup" rather than forcing a trade.
    """
    if not signal.setup_confirmed:
        return None

    ctx = signal.context
    sn = ctx.snapshot
    price = sn.current_price
    atr = sn.atr_14 or price * 0.02
    direction = signal.direction

    entry_low = entry_high = price
    stop_loss: float | None = None
    tp_values: list[float] = []
    invalidation = ""

    supports = sn.support_levels or [price * 0.97]
    resistances = sn.resistance_levels or [price * 1.03]
    nearest_support = supports[0]
    nearest_resistance = resistances[0]

    # Use stop-loss rule shapes from the spec when available.
    sl_atr_mult = None
    sl_support_buffer = None
    for rule in (spec.stop_loss_rules if spec else []):
        method = rule.get("method")
        if method == "atr_multiple":
            sl_atr_mult = float(rule.get("mult", 1.5))
        elif method in ("below_support", "inside_range"):
            sl_support_buffer = float(rule.get("buffer", 0.5))

    if direction == "LONG":
        lim_min = max(price * 0.995, nearest_support)
        entry_low = _round4(lim_min if lim_min < price else price * 0.995)
        entry_high = _round4(price)
        candidates = []
        if sl_support_buffer is not None:
            candidates.append(nearest_support - sl_support_buffer * atr)
        if sl_atr_mult is not None:
            candidates.append(price - sl_atr_mult * atr)
        if not candidates:
            candidates.append(nearest_support - 0.5 * atr)
        stop_loss = _round4(min(candidates))  # farthest stop = more conservative for longs
        risk = max(entry_high - stop_loss, price * 0.001)

        tp1 = nearest_resistance if nearest_resistance > entry_high else entry_high + 1.5 * risk
        tp1 = max(tp1, entry_high + 0.8 * risk)
        tp2 = tp1 + 1.0 * risk
        tp_values = [_round4(tp1), _round4(tp2)]
        invalidation = f"Close below {stop_loss:,.4f} invalidates the LONG thesis."
    elif direction == "SHORT":
        lim_max = min(price * 1.005, nearest_resistance)
        entry_high = _round4(lim_max if lim_max > price else price * 1.005)
        entry_low = _round4(price)
        candidates = []
        if sl_support_buffer is not None:
            candidates.append(nearest_resistance + sl_support_buffer * atr)
        if sl_atr_mult is not None:
            candidates.append(price + sl_atr_mult * atr)
        if not candidates:
            candidates.append(nearest_resistance + 0.5 * atr)
        stop_loss = _round4(max(candidates))
        risk = max(stop_loss - entry_low, price * 0.001)

        tp1 = nearest_support if nearest_support < entry_low else entry_low - 1.5 * risk
        tp1 = min(tp1, entry_low - 0.8 * risk)
        tp2 = tp1 - 1.0 * risk
        tp_values = [_round4(tp1), _round4(tp2)]
        invalidation = f"Close above {stop_loss:,.4f} invalidates the SHORT thesis."
    else:
        return None

    rr = round((abs(tp_values[0] - entry_high if direction == "LONG" else abs(entry_low - tp_values[0]))) / risk, 2)
    confidence = min(50 + 8 * signal.entry_rules_passed, 95) if signal.entry_rules_total else 50

    reasons = [r.detail for r in signal.rule_results if r.passed][:5]
    return TradePlan(
        symbol=signal.symbol,
        direction=direction,
        timeframe=signal.timeframe,
        strategy_key=signal.strategy_key,
        strategy_name=signal.strategy_name,
        entry_type="LIMIT",
        entry_low=entry_low,
        entry_high=entry_high,
        stop_loss=stop_loss,
        take_profits=tp_values,
        invalidation=invalidation,
        risk_reward=max(rr, 1.0),
        setup_status="VALID",
        stage=TradePlanStage.SETUP_FOUND,
        confidence=confidence,
        signal_reason=reasons,
        rule_results=signal.rule_results,
        context=ctx.to_dict(),
    )