"""Deterministic strategy rule engine (Phase 2E: Automated Trading).

Core principle from the architecture: the AI interprets and explains, but a
strategy must be expressed as structured, inspectable RULES that are evaluated
deterministically against a computed market context. The engine never invents
a signal because the user asked for one.
"""

from dataclasses import asdict, dataclass, field
from typing import Callable, Sequence

from app.services.assistant.indicators import (
    Candle,
    TechnicalSnapshot,
    compute_all_indicators,
)
from app.services.trading.indicators import calculate_stochastic, fibonacci_levels

__all__ = [
    "MarketContext",
    "RuleResult",
    "StrategySpec",
    "StrategySignal",
    "build_market_context",
    "evaluate_signal",
    "preset_specs",
    "spec_from_definition",
    "DEFAULT_TIMEFRAMES",
]

DEFAULT_TIMEFRAMES = ["15m", "1h", "4h", "1d"]


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


@dataclass
class MarketContext:
    symbol: str
    timeframe: str
    candles: list[Candle]
    snapshot: TechnicalSnapshot
    stoch_k: float | None
    stoch_d: float | None
    stoch_k_series: list[float | None]
    stoch_d_series: list[float | None]
    fib_levels: dict[str, float]
    avg_volume: float
    latest_candle_age_hrs: float | None

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "snapshot": self.snapshot.to_dict(),
            "stoch_k": self.stoch_k,
            "stoch_d": self.stoch_d,
            "fib_levels": self.fib_levels,
            "avg_volume": self.avg_volume,
        }


def build_market_context(
    symbol: str, timeframe: str, candles: Sequence[Candle]
) -> MarketContext:
    candles_list = list(candles)
    snapshot = compute_all_indicators(symbol, timeframe, candles_list)
    stoch_k, stoch_d, k_series, d_series = calculate_stochastic(candles_list)
    fib = fibonacci_levels(candles_list)
    volumes = [c.volume for c in candles_list[-20:]] if candles_list else []
    age_hrs = None
    if candles_list:
        age_ms = 0
        try:
            from datetime import datetime, timezone

            age_ms = datetime.now(timezone.utc).timestamp() * 1000 - candles_list[-1].timestamp
        except Exception:  # noqa: BLE001
            pass
        age_hrs = round(max(age_ms, 0) / 3_600_000, 2)
    return MarketContext(
        symbol=symbol.upper(),
        timeframe=timeframe,
        candles=candles_list,
        snapshot=snapshot,
        stoch_k=stoch_k,
        stoch_d=stoch_d,
        stoch_k_series=k_series,
        stoch_d_series=d_series,
        fib_levels=fib,
        avg_volume=round(_mean(volumes), 2) if volumes else 0.0,
        latest_candle_age_hrs=age_hrs,
    )


@dataclass
class RuleResult:
    name: str
    check: str
    passed: bool
    detail: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class StrategySpec:
    key: str
    name: str
    description: str = ""
    timeframes: list[str] = field(default_factory=lambda: list(DEFAULT_TIMEFRAMES))
    indicators: dict[str, bool] = field(default_factory=dict)
    entry_rules: list[dict] = field(default_factory=list)
    stop_loss_rules: list[dict] = field(default_factory=list)
    take_profit_rules: list[dict] = field(default_factory=list)
    risk_rules: list[dict] = field(default_factory=list)
    direction: str = "AUTO"  # AUTO | LONG | SHORT

    def to_json(self) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            "description": self.description,
            "timeframes": self.timeframes,
            "indicators": self.indicators,
            "entry_rules": self.entry_rules,
            "stop_loss_rules": self.stop_loss_rules,
            "take_profit_rules": self.take_profit_rules,
            "risk_rules": self.risk_rules,
            "direction": self.direction,
        }


def spec_from_definition(key: str, name: str, description: str, timeframes: str) -> StrategySpec:
    """Build a StrategySpec from the legacy free-text definition shape.

    Preset keys resolve to structured rules; everything else (custom strategies)
    keeps the structured container with empty rules, so the engine falls back to
    the deterministic heuristic entry check for them.
    """
    spec = preset_specs().get(key)
    if spec is not None:
        return spec
    return StrategySpec(
        key=key,
        name=name,
        description=description,
        timeframes=[t.strip() for t in (timeframes or "").split(",") if t.strip()]
        or list(DEFAULT_TIMEFRAMES),
    )


class _Presets:
    _cache: dict[str, StrategySpec] | None = None


def preset_specs() -> dict[str, StrategySpec]:
    if _Presets._cache is not None:
        return _Presets._cache
    _Presets._cache = {
        "trend_pullback": StrategySpec(
            key="trend_pullback",
            name="Trend Following / Pullback",
            description="Continuation entries on pullbacks to key moving averages inside an established trend.",
            indicators={"ema": True, "rsi": True, "atr": True, "support_resistance": True},
            entry_rules=[
                {"name": "Trend alignment", "check": "ema_fast_gt_slow", "params": {"fast": 20, "slow": 50}},
                {"name": "Pullback to dynamic support", "check": "price_above_ema", "params": {"period": 20}},
                {"name": "RSI normalised", "check": "rsi_gt", "params": {"value": 40}},
            ],
            stop_loss_rules=[
                {"method": "below_support", "buffer": 0.5},
                {"method": "atr_multiple", "mult": 1.5},
            ],
            take_profit_rules=[
                {"method": "next_resistance"},
                {"method": "rr_multiple", "r": 1.5},
            ],
            risk_rules=[{"name": "Minimum R:R", "check": "min_rr", "params": {"value": 1.2}}],
        ),
        "breakout": StrategySpec(
            key="breakout",
            name="Breakout & Momentum",
            description="Captures confirmed breaks of consolidation ranges with momentum confirmation.",
            indicators={"bollinger": True, "macd": True, "atr": True, "support_resistance": True},
            entry_rules=[
                {"name": "Coiling / consolidation", "check": "bb_width_range", "params": {"min": 0.02, "max": 0.13}},
                {"name": "Momentum confirmation", "check": "macd_hist_positive"},
            ],
            stop_loss_rules=[{"method": "inside_range"}, {"method": "atr_multiple", "mult": 1.2}],
            take_profit_rules=[{"method": "measured_move", "r": 1.0}, {"method": "rr_multiple", "r": 2.0}],
            risk_rules=[{"name": "Minimum R:R", "check": "min_rr", "params": {"value": 1.5}}],
        ),
        "mean_reversion": StrategySpec(
            key="mean_reversion",
            name="Mean Reversion / Counter-Trend",
            description="Exploits stretched prices outside Bollinger Bands with an extreme oscillator.",
            indicators={"bollinger": True, "rsi": True, "sma": True},
            entry_rules=[
                {"name": "RSI extreme", "check": "rsi_lt", "params": {"value": 30}},
                {"name": "Price at lower band", "check": "bb_reaction_lower"},
            ],
            stop_loss_rules=[{"method": "atr_multiple", "mult": 1.5}],
            take_profit_rules=[{"method": "band_middle"}, {"method": "rr_multiple", "r": 1.5}],
            risk_rules=[{"name": "Minimum R:R", "check": "min_rr", "params": {"value": 1.2}}],
        ),
        "momentum_reversal": StrategySpec(
            key="momentum_reversal",
            name="My Momentum Reversal Strategy",
            description="Bullish reversal after a downtrend exhausts: RSI turns up out of oversold with stochastic confirmation.",
            indicators={"rsi": True, "stochastic": True, "fibonacci": True, "support_resistance": True},
            entry_rules=[
                {"name": "Deep pullback reached", "check": "fib_pullback"},
                {"name": "Oversold condition", "check": "stoch_k_lt", "params": {"value": 25}},
                {"name": "Momentum turning", "check": "stoch_cross_up"},
                {"name": "Support interaction", "check": "price_at_support", "params": {"tolerance": 0.02}},
            ],
            stop_loss_rules=[{"method": "atr_multiple", "mult": 1.5}, {"method": "below_support"}],
            take_profit_rules=[{"method": "next_resistance"}, {"method": "rr_multiple", "r": 2.0}],
            risk_rules=[{"name": "Minimum R:R", "check": "min_rr", "params": {"value": 1.5}}],
        ),
        "general": StrategySpec(
            key="general",
            name="Full Technical Diagnostic",
            description="Synthesis of trend, momentum and structure when no dedicated strategy matches.",
            indicators={"ema": True, "rsi": True, "macd": True, "atr": True, "support_resistance": True},
            entry_rules=[
                {"name": "Dominant direction", "check": "trend_is", "params": {"value": "BULLISH"}},
            ],
            stop_loss_rules=[{"method": "atr_multiple", "mult": 1.5}, {"method": "below_support"}],
            take_profit_rules=[{"method": "next_resistance"}, {"method": "rr_multiple", "r": 1.5}],
            risk_rules=[{"name": "Minimum R:R", "check": "min_rr", "params": {"value": 1.2}}],
        ),
    }
    return _Presets._cache


# --------------------------------------------------------------------------- #
# Direction mirror: SHORT evaluation flips the sense of directional checks.
# --------------------------------------------------------------------------- #
_MIRROR: dict[str, str] = {
    "rsi_lt": "rsi_gt",
    "rsi_gt": "rsi_lt",
    "price_above_ema": "price_below_ema",
    "price_below_ema": "price_above_ema",
    "ema_fast_gt_slow": "ema_fast_lt_slow",
    "ema_fast_lt_slow": "ema_fast_gt_slow",
    "macd_hist_positive": "macd_hist_negative",
    "macd_hist_negative": "macd_hist_positive",
    "price_at_support": "price_at_resistance",
    "price_at_resistance": "price_at_support",
    "stoch_k_lt": "stoch_k_gt",
    "stoch_k_gt": "stoch_k_lt",
    "stoch_cross_up": "stoch_cross_down",
    "stoch_cross_down": "stoch_cross_up",
    "bb_reaction_lower": "bb_reaction_upper",
    "bb_reaction_upper": "bb_reaction_lower",
    "close_above_swing_high": "close_below_swing_low",
    "close_below_swing_low": "close_above_swing_high",
    "trend_is": "trend_is",  # handled specially (value swapped)
}


def _ema_value(ctx: MarketContext, period: int) -> float | None:
    key = {"20": "ema_20", "50": "ema_50", "200": "sma_200"}.get(str(period), "ema_20")
    return getattr(ctx.snapshot, key, None)


# --------------------------------------------------------------------------- #
# Check registry — implement check-name -> evaluator(ctx, params) -> (bool, str)
# --------------------------------------------------------------------------- #
def _ptolerance(params: dict) -> float:
    return float(params.get("tolerance", 0.01))


def _price(ctx: MarketContext) -> float:
    return ctx.snapshot.current_price or 0.0


def _nearest_support(ctx: MarketContext) -> float | None:
    return ctx.snapshot.support_levels[0] if ctx.snapshot.support_levels else None


def _nearest_resistance(ctx: MarketContext) -> float | None:
    return ctx.snapshot.resistance_levels[0] if ctx.snapshot.resistance_levels else None


def _c_check_min_rr(ctx: MarketContext, params: dict) -> tuple[bool, str]:
    value = float(params.get("value", 1.2))
    atr = ctx.snapshot.atr_14 or _price(ctx) * 0.02
    risk = max(atr, _price(ctx) * 0.001)
    reward = risk * value
    rr = round(reward / risk, 2)
    return rr >= value, f"Engineered R:R {rr} >= {value}"


_CHECKS: dict[str, Callable[[MarketContext, dict], tuple[bool, str]]] = {
    "rsi_lt": lambda ctx, p: (
        (ctx.snapshot.rsi_14 is not None and ctx.snapshot.rsi_14 < float(p.get("value", 30))),
        f"RSI {ctx.snapshot.rsi_14} < {p.get('value', 30)}",
    ),
    "rsi_gt": lambda ctx, p: (
        (ctx.snapshot.rsi_14 is not None and ctx.snapshot.rsi_14 > float(p.get("value", 30))),
        f"RSI {ctx.snapshot.rsi_14} > {p.get('value', 30)}",
    ),
    "trend_is": lambda ctx, p: (
        (ctx.snapshot.trend_bias == str(p.get("value", "BULLISH"))),
        f"Trend {ctx.snapshot.trend_bias} == {p.get('value', 'BULLISH')}",
    ),
    "price_above_ema": lambda ctx, p: (
        (_ema_value(ctx, int(p.get("period", 20))) is not None
         and _price(ctx) > _ema_value(ctx, int(p.get("period", 20)))),
        f"Price {_price(ctx):.4f} above EMA{int(p.get('period', 20))}",
    ),
    "price_below_ema": lambda ctx, p: (
        (_ema_value(ctx, int(p.get("period", 20))) is not None
         and _price(ctx) < _ema_value(ctx, int(p.get("period", 20)))),
        f"Price {_price(ctx):.4f} below EMA{int(p.get('period', 20))}",
    ),
    "ema_fast_gt_slow": lambda ctx, p: (
        (_ema_value(ctx, int(p.get("fast", 20))) is not None
         and _ema_value(ctx, int(p.get("slow", 50))) is not None
         and _ema_value(ctx, int(p.get("fast", 20))) > _ema_value(ctx, int(p.get("slow", 50)))),
        "EMA fast above slow",
    ),
    "ema_fast_lt_slow": lambda ctx, p: (
        (_ema_value(ctx, int(p.get("fast", 20))) is not None
         and _ema_value(ctx, int(p.get("slow", 50))) is not None
         and _ema_value(ctx, int(p.get("fast", 20))) < _ema_value(ctx, int(p.get("slow", 50)))),
        "EMA fast below slow",
    ),
    "macd_hist_positive": lambda ctx, p: (
        ctx.snapshot.macd_hist is not None and ctx.snapshot.macd_hist > 0,
        f"MACD histogram {(ctx.snapshot.macd_hist if ctx.snapshot.macd_hist is not None else 0):+.4f}",
    ),
    "macd_hist_negative": lambda ctx, p: (
        ctx.snapshot.macd_hist is not None and ctx.snapshot.macd_hist < 0,
        f"MACD histogram {(ctx.snapshot.macd_hist if ctx.snapshot.macd_hist is not None else 0):+.4f}",
    ),
    "price_at_support": lambda ctx, p: (
        _nearest_support(ctx) is not None
        and _price(ctx) > 0
        and abs(_price(ctx) - _nearest_support(ctx)) / _price(ctx) <= _ptolerance(p),
        f"Price within {_ptolerance(p) * 100:.1f}% of support {_nearest_support(ctx) or 'n/a':.4f}",
    ),
    "price_at_resistance": lambda ctx, p: (
        _nearest_resistance(ctx) is not None
        and _price(ctx) > 0
        and abs(_price(ctx) - _nearest_resistance(ctx)) / _price(ctx) <= _ptolerance(p),
        f"Price within {_ptolerance(p) * 100:.1f}% of resistance {_nearest_resistance(ctx) or 'n/a':.4f}",
    ),
    "stoch_k_lt": lambda ctx, p: (
        ctx.stoch_k is not None and ctx.stoch_k < float(p.get("value", 30)),
        f"Stoch %K {ctx.stoch_k} < {p.get('value', 30)}",
    ),
    "stoch_k_gt": lambda ctx, p: (
        ctx.stoch_k is not None and ctx.stoch_k > float(p.get("value", 70)),
        f"Stoch %K {ctx.stoch_k} > {p.get('value', 70)}",
    ),
    "stoch_cross_up": lambda ctx, p: _stoch_cross(ctx, "up"),
    "stoch_cross_down": lambda ctx, p: _stoch_cross(ctx, "down"),
    "fib_pullback": lambda ctx, p: (
        ctx.fib_levels.get("0.382") is not None
        and ctx.fib_levels.get("0.618") is not None
        and ctx.fib_levels["0.382"] < _price(ctx) <= ctx.fib_levels["0.618"],
        "Price inside 38.2%–61.8% retracement band",
    ),
    "volume_surge": lambda ctx, p: (
        ctx.avg_volume > 0
        and bool(ctx.candles)
        and ctx.candles[-1].volume > float(p.get("mult", 1.5)) * ctx.avg_volume,
        f"Last volume vs {p.get('mult', 1.5)}x 20-bar average",
    ),
    "bb_reaction_lower": lambda ctx, p: (
        ctx.snapshot.bb_lower is not None and _price(ctx) <= ctx.snapshot.bb_lower,
        f"Price at/below lower band {ctx.snapshot.bb_lower:.4f}",
    ),
    "bb_reaction_upper": lambda ctx, p: (
        ctx.snapshot.bb_upper is not None and _price(ctx) >= ctx.snapshot.bb_upper,
        f"Price at/above upper band {ctx.snapshot.bb_upper:.4f}",
    ),
    "bb_width_range": lambda ctx, p: _bb_width(ctx, p),
    "close_above_swing_high": lambda ctx, p: _swing_break(ctx, "high", int(p.get("lookback", 20))),
    "close_below_swing_low": lambda ctx, p: _swing_break(ctx, "low", int(p.get("lookback", 20))),
    "min_rr": _c_check_min_rr,
    "market_not_stale": lambda ctx, p: (
        ctx.latest_candle_age_hrs is not None and ctx.latest_candle_age_hrs <= 48,
        f"Latest candle age {ctx.latest_candle_age_hrs} h",
    ),
}


def _stoch_cross(ctx: MarketContext, direction: str) -> tuple[bool, str]:
    k, d = ctx.stoch_k_series, ctx.stoch_d_series
    if not k or not d:
        return False, "Stochastic series unavailable"
    k_last = next((v for v in reversed(k) if v is not None), None)
    d_last = next((v for v in reversed(d) if v is not None), None)
    if k_last is None or d_last is None:
        return False, "Stochastic values unavailable"
    prev_k = prev_d = None
    for kk, dd in zip(reversed(k), reversed(d)):
        if kk is not None and dd is not None:
            if (kk, dd) != (float(k_last), float(d_last)):
                prev_k, prev_d = kk, dd
                break
    if prev_k is None or prev_d is None:
        return False, "Insufficient stochastic history for cross detection"
    cross = (
        (prev_k <= prev_d and k_last > d_last)
        if direction == "up"
        else (prev_k >= prev_d and k_last < d_last)
    )
    return cross, f"Stoch cross {'UP' if direction == 'up' else 'DOWN'} (K {k_last:.1f} / D {d_last:.1f})"


def _bb_width(ctx: MarketContext, params: dict) -> tuple[bool, str]:
    price = _price(ctx)
    if price <= 0 or ctx.snapshot.bb_upper is None or ctx.snapshot.bb_lower is None:
        return False, "Bollinger bands unavailable"
    width = (ctx.snapshot.bb_upper - ctx.snapshot.bb_lower) / price
    lo, hi = float(params.get("min", 0.02)), float(params.get("max", 0.13))
    return lo <= width <= hi, f"Band width {width * 100:.1f}% in [{lo * 100:.0f}%, {hi * 100:.0f}%]"


def _swing_break(ctx: MarketContext, which: str, lookback: int) -> tuple[bool, str]:
    if len(ctx.candles) < 3:
        return False, "Not enough candles for swing break"
    prior = ctx.candles[-(lookback + 1) : -1]
    price = _price(ctx)
    if which == "high":
        ref = max(c.high for c in prior)
        ok = price > ref
        return ok, f"Price {price:.4f} above prior swing high {ref:.4f}"
    ref = min(c.low for c in prior)
    ok = price < ref
    return ok, f"Price {price:.4f} below prior swing low {ref:.4f}"


def _apply_direction(
    check: str, params: dict, direction: str
) -> tuple[str, dict]:
    """Flip a check for SHORT evaluation. LONG keeps it as-is."""
    if direction != "SHORT":
        return check, params
    if check == "trend_is":
        value = str(params.get("value", "BULLISH"))
        swapped = "BEARISH" if value == "BULLISH" else ("BULLISH" if value == "BEARISH" else value)
        return check, {**params, "value": swapped}
    return _MIRROR.get(check, check), params


@dataclass
class StrategySignal:
    symbol: str
    timeframe: str
    strategy_key: str
    strategy_name: str
    direction: str  # LONG | SHORT | FLAT
    setup_confirmed: bool
    entry_rules_passed: int
    entry_rules_total: int
    rule_results: list[RuleResult]
    blocked_reasons: list[str]
    context: MarketContext
    heuristic: bool = False

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "strategy_key": self.strategy_key,
            "strategy_name": self.strategy_name,
            "direction": self.direction,
            "setup_confirmed": self.setup_confirmed,
            "entry_rules_passed": self.entry_rules_passed,
            "entry_rules_total": self.entry_rules_total,
            "rule_results": [r.to_dict() for r in self.rule_results],
            "blocked_reasons": self.blocked_reasons,
            "context": self.context.to_dict(),
            "heuristic": self.heuristic,
        }


def _resolve_direction(spec: StrategySpec, ctx: MarketContext) -> str:
    requested = spec.direction.upper()
    if requested in ("LONG", "SHORT"):
        return requested
    bias = (ctx.snapshot.trend_bias or "NEUTRAL").upper()
    if bias == "BULLISH":
        return "LONG"
    if bias == "BEARISH":
        return "SHORT"
    return "FLAT"


def _heuristic_entry(
    spec_key: str,
    ctx: MarketContext,
    direction: str,
) -> tuple[bool, str, list[RuleResult]]:
    """Deterministic entry heuristic for rules-free (custom) strategies."""
    sn = ctx.snapshot
    rsi = sn.rsi_14
    trend = sn.trend_bias or "NEUTRAL"
    overextended = rsi is not None and (rsi <= 28 or rsi >= 78)
    if direction == "LONG":
        ok = trend == "BULLISH" and not overextended
        reason = (
            "Trend is BULLISH with RSI in a healthy zone for a long."
            if ok
            else f"Conditions do not favour entry (trend {trend}, RSI {rsi or 'n/a'})."
        )
    elif direction == "SHORT":
        ok = trend == "BEARISH" and not overextended
        reason = (
            "Trend is BEARISH with RSI in a healthy zone for a short."
            if ok
            else f"Conditions do not favour entry (trend {trend}, RSI {rsi or 'n/a'})."
        )
    else:
        ok = False
        reason = "No dominant directional structure — no entry."
    rule = RuleResult(name="Heuristic entry check", check="heuristic", passed=ok, detail=reason)
    return ok, reason, [rule]


def evaluate_signal(
    symbol: str,
    timeframe: str,
    spec: StrategySpec,
    candles: Sequence[Candle],
    *,
    direction_override: str | None = None,
) -> StrategySignal:
    """Run a strategy over a candle series and return a deterministic signal."""
    ctx = build_market_context(symbol, timeframe, candles)
    direction = (direction_override or spec.direction or "AUTO").upper()
    if direction in ("LONG", "SHORT"):
        pass
    else:
        direction = _resolve_direction(spec, ctx)

    rules = list(spec.entry_rules)
    if not rules:
        ok, reason, results = _heuristic_entry(spec.key, ctx, direction)
        if direction == "FLAT":
            ok = False
            reason = "No dominant directional structure — no entry."
        return StrategySignal(
            symbol=ctx.symbol,
            timeframe=timeframe,
            strategy_key=spec.key,
            strategy_name=spec.name,
            direction="FLAT" if direction == "FLAT" else direction,
            setup_confirmed=ok,
            entry_rules_passed=1 if ok else 0,
            entry_rules_total=1,
            rule_results=results,
            blocked_reasons=[] if ok else [reason],
            context=ctx,
            heuristic=True,
        )

    results: list[RuleResult] = []
    for rule in rules:
        check = str(rule.get("check", ""))
        params = dict(rule.get("params") or {})
        final_check, final_params = _apply_direction(check, params, direction)
        evaluator = _CHECKS.get(final_check)
        if evaluator is None:
            results.append(RuleResult(rule.get("name", check), check, True, "No-op (unknown rule)"))
            continue
        passed, detail = evaluator(ctx, final_params)
        results.append(RuleResult(str(rule.get("name", check)), check, passed, detail))

    passed_count = sum(1 for r in results if r.passed)
    blocked = [r.detail for r in results if not r.passed]
    confirmed = bool(rules) and passed_count == len(rules)
    if direction == "FLAT":
        confirmed = False
        blocked.insert(0, "No dominant directional market structure.")

    return StrategySignal(
        symbol=ctx.symbol,
        timeframe=timeframe,
        strategy_key=spec.key,
        strategy_name=spec.name,
        direction="FLAT" if direction == "FLAT" else direction,
        setup_confirmed=confirmed,
        entry_rules_passed=passed_count,
        entry_rules_total=len(results),
        rule_results=results,
        blocked_reasons=blocked,
        context=ctx,
    )