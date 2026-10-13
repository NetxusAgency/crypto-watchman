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
    evaluate_both: bool = False  # evaluate LONG and SHORT independently, pick the confirming side

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
            "evaluate_both": self.evaluate_both,
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
        "ict_liquidity": StrategySpec(
            key="ict_liquidity",
            name="ICT Liquidity Sweep + MSS + FVG",
            description=(
                "Direction-agnostic smart-money setup: price sweeps a liquidity pool "
                "(prior-day / Asian / equal swing highs-lows), shifts market structure, "
                "prints a displacement candle, then leaves an unmitigated fair value gap "
                "(FVG) whose midpoint (CE) is the limit entry. Stop beyond the sweep "
                "extreme; TP1 at internal liquidity (≥2R, 50% off + breakeven), TP2 at "
                "2.5R+ external liquidity. Entries only inside London/NY session windows."
            ),
            timeframes=["1m", "5m", "1h"],
            indicators={"atr": True},
            entry_rules=[
                {"name": "UTC session window (London/NY)", "check": "utc_session_open"},
                {"name": "Liquidity sweep & reclaim", "check": "ict_sweep"},
                {"name": "Market structure shift (MSS)", "check": "ict_mss"},
                {"name": "Displacement candle", "check": "ict_displacement"},
                {"name": "Unmitigated fair value gap", "check": "ict_fvg"},
                {"name": "Range fail-safe (<3x ATR)", "check": "volatility_calmer"},
            ],
            stop_loss_rules=[{"method": "beyond_sweep_extreme"}],
            take_profit_rules=[
                {"method": "internal_liquidity", "r": 2.0},
                {"method": "external_liquidity", "r": 2.5},
            ],
            risk_rules=[{"name": "Minimum R:R", "check": "min_rr", "params": {"value": 2.0}}],
            evaluate_both=True,
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


# --------------------------------------------------------------------------- #
# ICT Liquidity Sweep + MSS + FVG structure helpers (direction-agnostic).
# --------------------------------------------------------------------------- #
_ICT_CHECKS = {"ict_sweep", "ict_mss", "ict_displacement", "ict_fvg"}

# Approx UTC day bucket so prior-day / Asian levels can be derived from any
# intraday candle feed (timestamps are epoch ms).
_DAY_MS = 86_400_000


def _utc_hour(ts_ms: int) -> int:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).hour


def _day_label(ts_ms: int) -> int:
    return ts_ms // _DAY_MS


def _swing_pivots(
    candles: list[Candle], left: int = 2, right: int = 2
) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    """Fractal (left/right bars) swing highs and lows as (index, price) pairs."""
    highs: list[tuple[int, float]] = []
    lows: list[tuple[int, float]] = []
    n = len(candles)
    for i in range(left, n - right):
        before = range(i - left, i)
        after = range(i + 1, i + right + 1)
        if all(candles[i].high > candles[j].high for j in (*before, *after)):
            highs.append((i, candles[i].high))
        if all(candles[i].low < candles[j].low for j in (*before, *after)):
            lows.append((i, candles[i].low))
    return highs, lows


def _cluster_levels(values: list[float], tol: float) -> list[float]:
    """Merge nearby prices into equal-level liquidity pools (returns mid values)."""
    if not values:
        return []
    clusters: list[list[float]] = []
    for v in sorted(values):
        if clusters and v - clusters[-1][-1] <= tol:
            clusters[-1].append(v)
        else:
            clusters.append([v])
    return [round(sum(c) / len(c), 6) for c in clusters]


def _liquidity_sides(
    ctx: MarketContext, direction: str
) -> tuple[list[tuple[float, str]], list[tuple[float, str]]]:
    """Return (sweep-side pools, target-side pools) as (price, tag) tuples.

    LONG: sweep pools below price (PDL / Asian low / equal lows); targets above.
    SHORT: sweep pools above price (PDH / Asian high / equal highs); targets below.
    """
    candles = ctx.candles
    px = _price(ctx)
    n = len(candles)
    if n < 8 or px <= 0:
        return [], []
    atr = ctx.snapshot.atr_14 or px * 0.02 or 1.0
    last_day = _day_label(candles[-1].timestamp)
    prev = [c for c in candles if _day_label(c.timestamp) == last_day - 1]
    asia = [
        c for c in candles
        if _day_label(c.timestamp) == last_day and _utc_hour(c.timestamp) in (0, 1, 2, 3, 4)
    ]
    highs, lows = _swing_pivots(candles)

    if direction == "LONG":
        sweep_pools: list[tuple[float, str]] = []
        for tag, val in (
            ("PDL", min((c.low for c in prev), default=None)),
            ("AL", min((c.low for c in asia), default=None)),
        ):
            if val is not None and val < px:
                sweep_pools.append((round(val, 6), tag))
        recent_lows = [p for (i, p) in lows if i >= n - 48 and p < px]
        saw_liquidity = False
        for mid in _cluster_levels(recent_lows, 0.10 * atr):
            if mid < px:
                sweep_pools.append((mid, "EQ"))
                saw_liquidity = True
        if not sweep_pools and not saw_liquidity:
            sweep_pools.append((round(min(c.low for c in candles[-20:]), 6), "SW"))
        sweep_pools.sort(key=lambda x: x[0], reverse=True)
        target_pools: list[tuple[float, str]] = []
        for tag, val in (
            ("PDH", max((c.high for c in prev), default=None)),
            ("AH", max((c.high for c in asia), default=None)),
        ):
            if val is not None and val > px:
                target_pools.append((round(val, 6), tag))
        recent_highs = [p for (i, p) in highs if i >= n - 48 and p > px]
        for mid in _cluster_levels(recent_highs, 0.10 * atr):
            if mid > px:
                target_pools.append((mid, "EQ"))
        target_pools.sort()
        return sweep_pools, target_pools

    sweep_pools = []
    for tag, val in (
        ("PDH", max((c.high for c in prev), default=None)),
        ("AH", max((c.high for c in asia), default=None)),
    ):
        if val is not None and val > px:
            sweep_pools.append((round(val, 6), tag))
    recent_highs = [p for (i, p) in highs if i >= n - 48 and p > px]
    buy_liquidity = False
    for mid in _cluster_levels(recent_highs, 0.10 * atr):
        if mid > px:
            sweep_pools.append((mid, "EQ"))
            buy_liquidity = True
    if not sweep_pools and not buy_liquidity:
        sweep_pools.append((round(max(c.high for c in candles[-20:]), 6), "SW"))
    sweep_pools.sort(key=lambda x: x[0])
    target_pools = []
    for tag, val in (
        ("PDL", min((c.low for c in prev), default=None)),
        ("AL", min((c.low for c in asia), default=None)),
    ):
        if val is not None and val < px:
            target_pools.append((round(val, 6), tag))
    recent_lows = [p for (i, p) in lows if i >= n - 48 and p < px]
    for mid in _cluster_levels(recent_lows, 0.10 * atr):
        if mid < px:
            target_pools.append((mid, "EQ"))
    target_pools.sort(reverse=True)
    return sweep_pools, target_pools


def _ict_sweep_hit(
    ctx: MarketContext, sweep_pools: list[tuple[float, str]], direction: str
) -> dict | None:
    """A recent candle wicks beyond a liquidity pool and price reclaims it."""
    candles = ctx.candles
    n = len(candles)
    if n < 3:
        return None
    px = _price(ctx)
    start = n - 3
    if direction == "LONG":
        for lvl, tag in sweep_pools:
            hits = [i for i in range(start, n) if candles[i].low < lvl]
            if hits and px > lvl:
                return {
                    "level": lvl,
                    "tag": tag,
                    "sweep_idx": max(hits),
                    "sweep_price": min(candles[i].low for i in hits),
                }
        return None
    for lvl, tag in sweep_pools:
        hits = [i for i in range(start, n) if candles[i].high > lvl]
        if hits and px < lvl:
            return {
                "level": lvl,
                "tag": tag,
                "sweep_idx": max(hits),
                "sweep_price": max(candles[i].high for i in hits),
            }
    return None


def _ict_mss_after(ctx: MarketContext, sweep: dict, direction: str) -> dict | None:
    """Close beyond the most recent prior swing (low/high) after the sweep: MSS."""
    candles = ctx.candles
    n = len(candles)
    highs, lows = _swing_pivots(candles)
    if direction == "LONG":
        refs = [p for (i, p) in highs if i < sweep["sweep_idx"]]
        if not refs:
            return None
        ref = max(refs)
        for j in range(sweep["sweep_idx"] + 1, n):
            if candles[j].close > ref:
                return {"ref": ref, "idx": j, "price": candles[j].close}
        return None
    refs = [p for (i, p) in lows if i < sweep["sweep_idx"]]
    if not refs:
        return None
    ref = min(refs)
    for j in range(sweep["sweep_idx"] + 1, n):
        if candles[j].close < ref:
            return {"ref": ref, "idx": j, "price": candles[j].close}
    return None


def _ict_displacement(ctx: MarketContext, sweep: dict, direction: str) -> dict | None:
    """A candle after the sweep with body >= 1.5x the 10-bar average body and a
    decisive close (upper 70% of range for longs, lower 30% for shorts)."""
    candles = ctx.candles
    n = len(candles)
    i0 = max(0, n - 14)
    bodies = [abs(c.close - c.open) for c in candles[i0 : n - 3]]
    avg_body = _mean(bodies) or ((ctx.snapshot.atr_14 or 1.0) * 0.3)
    start = min(sweep["sweep_idx"] + 1, n - 1)
    for j in range(start, n):
        c = candles[j]
        rng = max(c.high - c.low, 1e-9)
        body = abs(c.close - c.open)
        if body < 1.5 * avg_body:
            continue
        if direction == "LONG":
            if c.close >= c.low + 0.70 * (c.high - c.low):
                return {"idx": j, "body": body}
        elif direction == "SHORT" and c.close <= c.low + 0.30 * (c.high - c.low):
            return {"idx": j, "body": body}
    return None


def _ict_fvg(ctx: MarketContext, sweep: dict, direction: str) -> dict | None:
    """Find the fair value gap: the displacement candle leaves one. For longs the
    gap is candle1.high < candle3.low; CE (entry) = mid of the gap zone."""
    candles = ctx.candles
    n = len(candles)
    atr = ctx.snapshot.atr_14 or _price(ctx) * 0.02 or 1.0
    px = _price(ctx)
    for i in range(sweep["sweep_idx"], n - 2):
        c1, c3 = candles[i], candles[i + 2]
        if direction == "LONG":
            if not (c1.high < c3.low):
                continue
            bottom, top = c1.high, c3.low
        else:
            if not (c1.low > c3.high):
                continue
            top, bottom = c1.low, c3.high
        ce = (top + bottom) / 2.0
        # Unmitigated: price still near the CE and has not run through the zone.
        if not (bottom - atr * 0.25 <= px <= top + atr * 0.25):
            continue
        if n - 1 - (i + 2) > 1:
            continue
        return {"c1": i, "c2": i + 1, "c3": i + 2, "bottom": bottom, "top": top, "ce": ce}
    return None


def _ict_analysis(ctx: MarketContext, direction: str) -> dict | None:
    """Full ICT sequence for one direction, or None when the structure is absent."""
    if direction not in ("LONG", "SHORT"):
        return None
    sweep_pools, target_pools = _liquidity_sides(ctx, direction)
    sweep = _ict_sweep_hit(ctx, sweep_pools, direction)
    if not sweep:
        return None
    mss = _ict_mss_after(ctx, sweep, direction)
    if not mss:
        return None
    displacement = _ict_displacement(ctx, sweep, direction)
    if not displacement:
        return None
    fvg = _ict_fvg(ctx, sweep, direction)
    if not fvg or fvg["c2"] != displacement["idx"]:
        return None
    return {
        "direction": direction,
        "sweep": sweep,
        "mss": mss,
        "displacement": displacement,
        "fvg": fvg,
        "target_pools": target_pools,
    }


def _ict_plan_levels(ctx: MarketContext, ict: dict, direction: str) -> dict | None:
    """Derive entry (CE limit), stop (beyond sweep extreme) and 2R+/2.5R+ targets."""
    px = _price(ctx) or 0.0
    if px <= 0:
        return None
    atr = ctx.snapshot.atr_14 or px * 0.02 or 1.0
    fvg = ict["fvg"]
    entry = fvg["ce"]
    sweep_price = ict["sweep"]["sweep_price"]
    buffer = max(px * 0.0001, 0.10 * atr)
    if direction == "LONG" and entry - sweep_price > 0:
        stop = sweep_price - buffer
        risk = max(entry - stop, px * 0.001)
    elif direction == "SHORT" and sweep_price - entry > 0:
        stop = sweep_price + buffer
        risk = max(stop - entry, px * 0.001)
    else:
        return None

    pools = [p for (p, _tag) in ict["target_pools"]]
    tps: list[float] = []
    if direction == "LONG":
        for p in sorted(p for p in pools if p > entry):
            rr = (p - entry) / risk
            if rr >= 2.5 and len(tps) == 1:
                tps.append(p)
                break
            if rr >= 2.0 and not tps:
                tps.append(p)
    else:
        for p in sorted((p for p in pools if p < entry), reverse=True):
            rr = (entry - p) / risk
            if rr >= 2.5 and len(tps) == 1:
                tps.append(p)
                break
            if rr >= 2.0 and not tps:
                tps.append(p)
    if not tps:
        tps.append(entry + (2.0 * risk if direction == "LONG" else -2.0 * risk))
    if len(tps) == 1:
        tps.append(tps[0] + (risk if direction == "LONG" else -risk))
    tp1, tp2 = tps[0], tps[1]
    risk_reward = round(abs(tp1 - entry) / risk, 2)
    return {
        "entry": round(entry, 6),
        "stop": round(stop, 6),
        "take_profits": [round(tp1, 6), round(tp2, 6)],
        "risk_reward": max(risk_reward, 2.0),
        "sweep_level": ict["sweep"]["level"],
        "liquidity_tag": ict["sweep"]["tag"],
        "ce": round(entry, 6),
    }


def _c_utc_session_open(ctx: MarketContext, params: dict) -> tuple[bool, str]:
    if not ctx.candles:
        return False, "No candle data for session window."
    hour = _utc_hour(ctx.candles[-1].timestamp)
    name = ("London open" if hour in (7, 8, 9)
            else ("New York open" if hour in (13, 14, 15) else ""))
    ok = bool(name)
    return ok, (
        f"UTC hour {hour:02d}:00 inside {name} window (07–10 London / 13–16 NY) — entries allowed"
        if ok else f"UTC hour {hour:02d}:00 outside London/NY session — no entries"
    )


def _c_volatility_calmer(ctx: MarketContext, params: dict) -> tuple[bool, str]:
    if not ctx.candles:
        return False, "No candle data."
    last = ctx.candles[-1]
    rng = max(last.high - last.low, 0.0)
    atr = ctx.snapshot.atr_14 or rng or _price(ctx) * 0.02 or 1.0
    ok = rng <= 3.0 * atr
    return ok, f"Last range {rng:.4f} ≤ 3×ATR {3.0 * atr:.4f} (fail-safe: no entry in >3×ATR expansion)"


def _c_ict_sweep(ctx: MarketContext, params: dict) -> tuple[bool, str]:
    st = params.get("_ict")
    direction = params.get("direction", "LONG")
    if not st:
        return False, f"No {direction} liquidity sweep (pool pierced and reclaimed within 3 candles)."
    s = st["sweep"]
    return True, f"{direction}: {s['tag']} liquidity {s['level']:,.4f} swept to {s['sweep_price']:,.4f} and reclaimed"


def _c_ict_mss(ctx: MarketContext, params: dict) -> tuple[bool, str]:
    st = params.get("_ict")
    direction = params.get("direction", "LONG")
    if not st or not st.get("mss"):
        return False, f"No {direction} market structure shift yet."
    m = st["mss"]
    return True, f"{direction} MSS: close {m['price']:,.4f} beyond prior swing {m['ref']:,.4f}"


def _c_ict_displacement(ctx: MarketContext, params: dict) -> tuple[bool, str]:
    st = params.get("_ict")
    direction = params.get("direction", "LONG")
    if not st or not st.get("displacement"):
        return False, f"No {direction} displacement candle (body ≥1.5× average, decisive close)."
    d = st["displacement"]
    return True, f"{direction} displacement body {d['body']:,.4f} at bar {d['idx']}"


def _c_ict_fvg(ctx: MarketContext, params: dict) -> tuple[bool, str]:
    st = params.get("_ict")
    direction = params.get("direction", "LONG")
    if not st or not st.get("fvg"):
        return False, f"No unmitigated {direction} fair value gap (FVG)."
    f = st["fvg"]
    return True, f"{direction} FVG {f['bottom']:,.4f}–{f['top']:,.4f} (CE {f['ce']:,.4f}) at bar {f['c2']}"


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
    "utc_session_open": _c_utc_session_open,
    "volatility_calmer": _c_volatility_calmer,
    "ict_sweep": _c_ict_sweep,
    "ict_mss": _c_ict_mss,
    "ict_displacement": _c_ict_displacement,
    "ict_fvg": _c_ict_fvg,
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
    check: str,
    params: dict,
    direction: str,
    ict_structure: dict | None = None,
) -> tuple[str, dict]:
    """Flip a check for SHORT evaluation. LONG keeps it as-is."""
    if check in _ICT_CHECKS:
        return check, {**params, "direction": direction, "_ict": ict_structure}
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
    ict: dict | None = None  # ICT plan levels (CE entry, sweep stop, 2R+ targets)

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
            "ict": self.ict,
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


def _evaluate_one(
    ctx: MarketContext,
    spec: StrategySpec,
    direction: str,
) -> StrategySignal:
    """Evaluate a strategy for a single resolved direction."""
    rules = list(spec.entry_rules)
    ict_structure = None
    if direction in ("LONG", "SHORT") and any(
        str(rule.get("check", "")) in _ICT_CHECKS for rule in rules
    ):
        ict_structure = _ict_analysis(ctx, direction)

    if not rules:
        ok, reason, results = _heuristic_entry(spec.key, ctx, direction)
        if direction == "FLAT":
            ok = False
            reason = "No dominant directional structure — no entry."
        return StrategySignal(
            symbol=ctx.symbol,
            timeframe=ctx.timeframe,
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
        final_check, final_params = _apply_direction(check, params, direction, ict_structure)
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

    signal = StrategySignal(
        symbol=ctx.symbol,
        timeframe=ctx.timeframe,
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
    if confirmed and ict_structure is not None:
        levels = _ict_plan_levels(ctx, ict_structure, direction)
        if levels:
            signal.ict = levels
    return signal


def evaluate_signal(
    symbol: str,
    timeframe: str,
    spec: StrategySpec,
    candles: Sequence[Candle],
    *,
    direction_override: str | None = None,
) -> StrategySignal:
    """Run a strategy over a candle series and return a deterministic signal.

    `spec.evaluate_both` evaluates LONG and SHORT independently and returns the
    side whose full sequence (liquidity sweep → MSS → displacement → FVG)
    confirms, so structure decides — never a pre-baked market bias.
    """
    ctx = build_market_context(symbol, timeframe, candles)
    requested = (direction_override or spec.direction or "AUTO").upper()
    if requested in ("LONG", "SHORT"):
        return _evaluate_one(ctx, spec, requested)
    if spec.evaluate_both:
        long_sig = _evaluate_one(ctx, spec, "LONG")
        short_sig = _evaluate_one(ctx, spec, "SHORT")
        if long_sig.setup_confirmed and short_sig.setup_confirmed:
            lr = (long_sig.ict or {}).get("risk_reward", 0.0) if long_sig.ict else 0.0
            sr = (short_sig.ict or {}).get("risk_reward", 0.0) if short_sig.ict else 0.0
            return long_sig if lr >= sr else short_sig
        if long_sig.setup_confirmed:
            return long_sig
        if short_sig.setup_confirmed:
            return short_sig
        return StrategySignal(
            symbol=ctx.symbol,
            timeframe=timeframe,
            strategy_key=spec.key,
            strategy_name=spec.name,
            direction="FLAT",
            setup_confirmed=False,
            entry_rules_passed=long_sig.entry_rules_passed + short_sig.entry_rules_passed,
            entry_rules_total=long_sig.entry_rules_total + short_sig.entry_rules_total,
            rule_results=long_sig.rule_results + short_sig.rule_results,
            blocked_reasons=(long_sig.blocked_reasons or []) + (short_sig.blocked_reasons or []),
            context=ctx,
        )
    direction = _resolve_direction(spec, ctx)
    return _evaluate_one(ctx, spec, direction)