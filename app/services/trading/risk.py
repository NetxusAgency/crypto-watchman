"""Risk engine — the hard gate between trade plans and execution.

Every proposed trade must pass the full check list below before the paper
gateway (or, in a future authorized phase, a live adapter) may touch it.
The AI can generate the plan; it can never bypass this module.
"""

from dataclasses import asdict, dataclass

from app.services.prices.price_fetcher import CRYPTO_MAPPING
from app.services.trading.trade_plan import TradePlan

__all__ = ["RiskCheck", "RiskReport", "RiskEngine"]


def _is_forex_symbol(symbol: str) -> bool:
    s = symbol.upper().replace("/", "").replace("-", "")
    if len(s) != 6:
        return False
    fiat = {"USD", "EUR", "GBP", "JPY", "CHF", "AUD", "CAD", "NZD"}
    return s[:3] in fiat and s[3:] in fiat


@dataclass
class RiskCheck:
    name: str
    passed: bool
    detail: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RiskReport:
    plan_id: str
    passed: bool
    checks: list[RiskCheck]
    blocked_reasons: list[str]

    def to_dict(self) -> dict:
        return {
            "plan_id": self.plan_id,
            "passed": self.passed,
            "checks": [c.to_dict() for c in self.checks],
            "blocked_reasons": self.blocked_reasons,
        }


class RiskEngine:
    def __init__(
        self,
        max_risk_per_trade_pct: float = 2.0,
        max_daily_loss_pct: float = 5.0,
    ):
        self.max_risk_per_trade_pct = max_risk_per_trade_pct
        self.max_daily_loss_pct = max_daily_loss_pct

    def assess(
        self,
        plan: TradePlan,
        *,
        equity: float = 10_000.0,
        open_symbols: list[str] | None = None,
        today_realized_pnl: float = 0.0,
        position_size_usd: float | None = None,
        latest_candle_age_hrs: float | None = None,
    ) -> RiskReport:
        """Evaluate a plan against the full risk gate. Never raises on a bad plan."""
        checks: list[RiskCheck] = []
        open_symbols = {s.upper() for s in (open_symbols or [])}

        if latest_candle_age_hrs is None:
            latest_candle_age_hrs = plan.context.get("latest_candle_age_hrs")

        # 1. Market open / data fresh
        fresh = (
            latest_candle_age_hrs is None
            or latest_candle_age_hrs <= 48
        )
        checks.append(RiskCheck(
            "market_open",
            fresh,
            "Candle data is fresh." if fresh else f"Latest candle is {latest_candle_age_hrs} h old — market likely closed.",
        ))

        # 2. Symbol supported
        supported = plan.symbol in CRYPTO_MAPPING or _is_forex_symbol(plan.symbol)
        checks.append(RiskCheck(
            "symbol_supported",
            supported,
            f"{plan.symbol} is monitored by the price engine."
            if supported else f"{plan.symbol} is not in the supported symbol set.",
        ))

        # 3. Verified strategy + valid setup
        strategy_ok = bool(plan.strategy_key) and plan.setup_status == "VALID"
        checks.append(RiskCheck(
            "strategy_valid",
            strategy_ok,
            f"Strategy {plan.strategy_key} produced a VALID setup."
            if strategy_ok else "No valid strategy setup behind this plan.",
        ))

        # 4. No duplicate position
        dup = plan.symbol in open_symbols
        checks.append(RiskCheck(
            "no_duplicate_position",
            not dup,
            "No open position on this symbol."
            if not dup else f"Position already open on {plan.symbol}.",
        ))

        # 5. Stop loss present and sane
        direction = plan.direction.upper()
        has_stop = plan.stop_loss is not None and plan.stop_loss > 0
        if direction == "LONG":
            stop_sane = has_stop and plan.stop_loss < plan.entry_high
        elif direction == "SHORT":
            stop_sane = has_stop and plan.stop_loss > plan.entry_low
        else:
            stop_sane = False
        checks.append(RiskCheck(
            "stop_loss_present",
            stop_sane,
            f"Stop {plan.stop_loss:.4f} on the {direction} side of entry."
            if stop_sane else "Missing or invalid stop loss.",
        ))

        # 6. Position sizing feasible within the risk budget and 1x equity
        size = self.position_size(plan, equity)
        price = plan.context.get("snapshot", {}).get("current_price") or plan.entry_high
        notional = size * price if size and price else None
        risk_dollars = (size * abs(plan.stop_loss - price)) if size else None
        sized = notional is not None and notional <= equity
        checks.append(RiskCheck(
            "risk_budget",
            bool(sized),
            f"Sized {size} {plan.symbol} ~ ${notional:,.2f} notional, risk ${risk_dollars:,.2f} ≤ {self.max_risk_per_trade_pct}% of equity."
            if sized else "Position cannot be sized within the risk budget without leverage.",
        ))

        # 7. Minimum R:R
        rr_ok = plan.risk_reward >= 1.0
        checks.append(RiskCheck(
            "min_risk_reward",
            rr_ok,
            f"R:R {plan.risk_reward} ≥ 1.0" if rr_ok else f"R:R {plan.risk_reward} below minimum.",
        ))

        # 8. Daily loss limit
        daily_ok = today_realized_pnl >= -(self.max_daily_loss_pct / 100.0) * equity
        checks.append(RiskCheck(
            "daily_loss_limit",
            daily_ok,
            f"Today's realized P&L {today_realized_pnl:+,.2f} within limit."
            if daily_ok else "Daily loss limit would be exceeded.",
        ))

        blocked = [c.detail for c in checks if not c.passed]
        return RiskReport(
            plan_id=plan.plan_id,
            passed=not blocked,
            checks=checks,
            blocked_reasons=blocked,
        )

    def position_size(
        self,
        plan: TradePlan,
        equity: float = 10_000.0,
    ) -> float | None:
        """Size a position so that the stop-distance risk equals the risk budget."""
        risk_dollars = equity * (self.max_risk_per_trade_pct / 100.0)
        price = float(plan.context.get("snapshot", {}).get("current_price") or 0)
        stop_dist = abs(plan.stop_loss - price) if plan.stop_loss else 0
        if price <= 0 or stop_dist <= 0:
            return None
        size = risk_dollars / stop_dist
        return round(size, 6)