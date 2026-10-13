import math
import random
from datetime import datetime, timezone

from app.services.assistant.indicators import Candle
from app.services.trading import (
    RiskEngine,
    TradePlan,
    build_market_context,
    build_trade_plan,
    calculate_stochastic,
    evaluate_signal,
    fibonacci_levels,
    preset_specs,
    spec_from_definition,
)
from app.services.trading.strategy_engine import StrategySpec


def _candles(closes, base=100.0):
    candles = []
    prev = base
    for i, v in enumerate(closes):
        high = max(prev, v) * 1.003
        low = min(prev, v) * 0.997
        candles.append(
            Candle(
                timestamp=(i + 1) * 3_600_000,
                open=round(prev, 4),
                high=round(high, 4),
                low=round(low, 4),
                close=round(v, 4),
                volume=1000.0 + (i % 5) * 50,
            )
        )
        prev = v
    return candles


def _bullish_series(n=160):
    closes = []
    c = 100.0
    for i in range(n):
        c *= 1.0022
        if i % 9 < 4:
            c *= 0.999
        closes.append(c)
    return closes


def _bearish_series(n=160):
    closes = []
    c = 140.0
    for i in range(n):
        c /= 1.0023
        if i % 9 < 4:
            c *= 1.0012
        closes.append(c)
    return closes


def _selloff_series(n=120, drop=25):
    closes = []
    c = 100.0
    for _ in range(n - drop):
        c *= 1.001
        closes.append(c)
    for _ in range(drop):
        c *= 0.98
        closes.append(c)
    return closes


def _coiled_series(n=100):
    closes = []
    c = 100.0
    for _ in range(40):
        c *= 1.0015
        closes.append(c)
    top = c
    for i in range(58):
        closes.append(top * (1 + 0.008 * math.sin(i / 4)))
    closes.append(closes[-1] * 1.004)
    closes.append(closes[-2] * 1.006)
    return closes


def _flat_series(n=120):
    return [100.0] * n


def _crash_tail(closes, k=3):
    out = list(closes)
    for mult in (0.97, 0.95, 0.85)[:k]:
        out.append(out[-1] * mult)
    return out


def _raise_series(n=120):
    closes = []
    c = 100.0
    for _ in range(n):
        c *= 1.001
        closes.append(c)
    return closes


def _signal(symbol, timeframe, key, closes, direction=None):
    spec = preset_specs()[key]
    return evaluate_signal(
        symbol, timeframe, spec, _candles(closes), direction_override=direction
    )


class TestStochastic:
    def test_rising_market_high_k(self):
        candles = _candles(_bullish_series())
        k, d, ks, ds = calculate_stochastic(candles)
        assert k is not None
        assert k > 50

    def test_falling_market_low_k(self):
        candles = _candles(_bearish_series())
        k, d, ks, ds = calculate_stochastic(candles)
        assert k is not None
        assert k < 50

    def test_series_alignment(self):
        candles = _candles(_bullish_series(n=60))
        k, d, ks, ds = calculate_stochastic(candles)
        assert len(ks) == len(candles)
        assert len(ds) == len(candles)
        assert ks[-1] is not None and ds[-1] is not None


class TestFibonacci:
    def test_levels_between_swing(self):
        scalers = [1.0 + math.sin(i / 4) * 0.005 for i in range(60)]
        closes = [100.0]
        for s in scalers[1:]:
            closes.append(closes[-1] * (1 + (s - 1) * 2))
        candles = _candles(closes)
        fib = fibonacci_levels(candles, lookback=30)
        assert set(fib.keys()) == {"0", "0.236", "0.382", "0.5", "0.618", "0.786", "1"}
        keys = ["0", "0.236", "0.382", "0.5", "0.618", "0.786", "1"]
        assert all(fib[keys[i]] > fib[keys[i + 1]] for i in range(len(keys) - 1))

    def test_empty_when_no_range(self):
        assert fibonacci_levels([]) == {}


class TestTrendPullback:
    def test_bullish_confirms_long(self):
        signal = _signal("BTC", "4h", "trend_pullback", _bullish_series())
        assert signal.setup_confirmed is True
        assert signal.direction == "LONG"
        assert signal.entry_rules_passed == signal.entry_rules_total == 3

    def test_bearish_confirms_short(self):
        signal = _signal("BTC", "4h", "trend_pullback", _bearish_series(), direction="SHORT")
        assert signal.setup_confirmed is True
        assert signal.direction == "SHORT"

    def test_flat_market_not_confirmed(self):
        signal = _signal("BTC", "4h", "trend_pullback", _flat_series())
        assert signal.setup_confirmed is False
        assert signal.blocked_reasons


class TestMeanReversion:
    def test_oversold_at_lower_band_confirms_long(self):
        signal = _signal(
            "ETH", "4h", "mean_reversion", _crash_tail(_selloff_series()), direction="LONG"
        )
        assert signal.setup_confirmed is True
        assert signal.direction == "LONG"

    def test_rally_rejects_long(self):
        signal = _signal("ETH", "4h", "mean_reversion", _raise_series())
        assert signal.setup_confirmed is False


class TestBreakout:
    def test_coiled_with_momentum_confirms(self):
        signal = _signal("SOL", "1h", "breakout", _coiled_series())
        assert signal.setup_confirmed is True


class TestGeneral:
    def test_requires_dominant_structure(self):
        signal = _signal("BTC", "4h", "general", _flat_series())
        assert signal.setup_confirmed is False

    def test_bullish_confirms_general(self):
        signal = _signal("BTC", "4h", "general", _bullish_series())
        assert signal.setup_confirmed is True
        assert signal.direction == "LONG"


class TestMomentumReversal:
    def test_rules_total_and_result_shape(self):
        spec = preset_specs()["momentum_reversal"]
        candles = _candles(_selloff_series())
        from app.services.trading.strategy_engine import evaluate_signal

        signal = evaluate_signal("BTC", "4h", spec, candles)
        assert signal.entry_rules_total == 4
        assert len(signal.rule_results) == 4
        for r in signal.rule_results:
            assert isinstance(r.passed, bool)
            assert r.name


class TestCustomHeuristic:
    def test_freeform_spec_uses_heuristic(self):
        spec = spec_from_definition(
            "custom_5", "My Rules", "Imported rules-text strategy", "15m,1h,4h,1d"
        )
        assert not spec.entry_rules
        candles = _candles(_bullish_series())
        signal = evaluate_signal("BTC", "4h", spec, candles)
        assert signal.heuristic is True
        assert signal.direction == "LONG"

    def test_preset_resolves_structurally(self):
        spec = spec_from_definition("trend_pullback", "x", "y", "4h")
        assert spec is preset_specs()["trend_pullback"]
        assert spec.entry_rules


class TestTradePlan:
    def test_long_levels_ordered(self):
        spec = preset_specs()["trend_pullback"]
        candles = _candles(_bullish_series())
        signal = evaluate_signal("BTC", "4h", spec, candles)
        assert signal.setup_confirmed
        plan = build_trade_plan(signal, spec)
        assert plan is not None
        assert plan.direction == "LONG"
        assert plan.stop_loss < plan.entry_low <= plan.entry_high < plan.take_profits[0] < plan.take_profits[1]
        assert plan.risk_reward >= 1.0
        assert plan.take_profits[0] > 0

    def test_short_levels_ordered(self):
        spec = preset_specs()["trend_pullback"]
        candles = _candles(_bearish_series())
        signal = evaluate_signal("BTC", "4h", spec, candles, direction_override="SHORT")
        assert signal.setup_confirmed
        plan = build_trade_plan(signal, spec)
        assert plan is not None
        assert plan.direction == "SHORT"
        assert plan.stop_loss > plan.entry_high >= plan.entry_low > plan.take_profits[0] > plan.take_profits[1]

    def test_none_when_not_confirmed(self):
        signal = _signal("BTC", "4h", "general", _flat_series())
        assert build_trade_plan(signal) is None

    def test_plan_payload_flag(self):
        signal = _signal("BTC", "4h", "trend_pullback", _bullish_series())
        plan = build_trade_plan(signal, preset_specs()["trend_pullback"])
        payload = plan.to_dict()
        assert payload["verified_for_live"] is True
        assert payload["stage"] == "SETUP_FOUND"
        assert payload["plan_id"].startswith("TP-")


def _manual_plan(symbol="BTC", stop=90.0, entry_low=98.0, entry_high=100.0, tp=110.0, rr=1.8):
    return TradePlan(
        plan_id="TP-X",
        symbol=symbol,
        direction="LONG",
        timeframe="4h",
        strategy_key="trend_pullback",
        strategy_name="Trend Following / Pullback",
        entry_low=entry_low,
        entry_high=entry_high,
        stop_loss=stop,
        take_profits=[tp, tp + 5.0],
        invalidation="test",
        risk_reward=rr,
        context={
            "snapshot": {"current_price": entry_high},
            "latest_candle_age_hrs": 1.0,
            "symbol": symbol,
        },
    )


class TestRiskEngine:
    def setup_method(self):
        self.risk = RiskEngine(max_risk_per_trade_pct=2.0, max_daily_loss_pct=5.0)

    def test_passes_valid_plan(self):
        report = self.risk.assess(_manual_plan())
        assert report.passed is True
        assert not report.blocked_reasons
        assert len(report.checks) == 8

    def test_rejects_duplicate_position(self):
        report = self.risk.assess(_manual_plan(), open_symbols=["BTC"])
        assert report.passed is False
        assert any(c.name == "no_duplicate_position" and not c.passed for c in report.checks)

    def test_rejects_unsupported_symbol(self):
        plan = _manual_plan(symbol="NOTREAL")
        report = self.risk.assess(plan)
        assert report.passed is False

    def test_rejects_stale_market(self):
        plan = _manual_plan()
        report = self.risk.assess(plan, latest_candle_age_hrs=200)
        assert not report.passed
        assert any(c.name == "market_open" and not c.passed for c in report.checks)

    def test_rejects_daily_loss_breach(self):
        report = self.risk.assess(_manual_plan(), equity=10000, today_realized_pnl=-800)
        assert not report.passed

    def test_rejects_missing_stop(self):
        plan = _manual_plan(stop=0.0)
        report = self.risk.assess(plan)
        assert not report.passed
        assert any(c.name == "stop_loss_present" and not c.passed for c in report.checks)

    def test_rejects_low_rr(self):
        report = self.risk.assess(_manual_plan(rr=0.5))
        assert not report.passed
        assert any(c.name == "min_risk_reward" and not c.passed for c in report.checks)

    def test_position_size_respects_budget(self):
        plan = _manual_plan()
        report = self.risk.assess(plan, equity=10000)
        assert report.passed
        size = self.risk.position_size(plan, equity=10000)
        assert size is not None and size > 0
        risk_dollars = size * abs(plan.stop_loss - plan.entry_high)
        assert risk_dollars <= 200.0
        assert size * plan.entry_high <= 10000.0


# --------------------------------------------------------------------------- #
# ICT Liquidity Sweep + MSS + FVG preset tests.
# --------------------------------------------------------------------------- #
def _ict_bullish_plan():
    """Range building equal lows, then sweep -> displacement (MSS) -> FVG (CE)."""
    rows = []
    prev = 100.0
    for i in range(45):
        c = 100.0 + 0.55 * math.sin(i / 3.0)
        rows.append((prev, c + 0.05, c - 0.05, c))
        prev = c
    base0 = prev
    rows.append((base0 + 0.02, base0 + 0.08, base0 - 0.10, base0 + 0.02))   # quiet
    rows.append((base0 + 0.02, base0 + 0.05, base0 - 1.20, base0 - 0.60))   # SWEEP below pool
    rows.append((base0 - 0.60, base0 + 0.95, base0 - 0.65, base0 + 0.85))   # displacement / MSS
    rows.append((base0 + 0.55, base0 + 0.65, base0 + 0.22, base0 + 0.28))   # c3, close inside gap
    return rows


def _ict_bearish_plan():
    """Mirror-image sweep -> displacement -> FVG for the SHORT side."""
    bull = _ict_bullish_plan()
    mid = 100.5
    return [
        (round(2 * mid - o, 6), round(2 * mid - l, 6), round(2 * mid - h, 6), round(2 * mid - c, 6))
        for (o, h, l, c) in bull
    ]


def _ict_climb_plan(n=50):
    """A clean, steady uptrend: bullish bias but no sweep/MSS/FVG structure."""
    rows = []
    prev = 100.0
    for i in range(n):
        c = prev + 0.05 + 0.02 * math.sin(i / 4.0)
        rows.append((prev + 0.01, c + 0.10, c - 0.10, c))
        prev = c
    return rows


def _ict_candles(plan, start_hour=13, step_ms=3_600_000):
    """Candles starting at `start_hour` UTC spaced by `step_ms`."""
    candles = []
    base_ts = int(
        datetime(2024, 1, 1, start_hour, 0, 0, tzinfo=timezone.utc).timestamp() * 1000
    )
    for i, (o, h, l, c) in enumerate(plan):
        candles.append(
            Candle(
                timestamp=base_ts + i * step_ms,
                open=round(o, 6),
                high=round(h, 6),
                low=round(l, 6),
                close=round(c, 6),
                volume=1000.0,
            )
        )
    return candles


def _ict_bias_series(kind="bull"):
    """A 50-bar 1H series with an unambiguous BULLISH / BEARISH / NEUTRAL bias."""
    if kind == "bull":
        return _ict_candles(_ict_climb_plan(50))
    if kind == "bear":
        mid = 103.5
        bear = [
            (round(2 * mid - o, 6), round(2 * mid - l, 6), round(2 * mid - h, 6), round(2 * mid - c, 6))
            for (o, h, l, c) in _ict_climb_plan(50)
        ]
        return _ict_candles(bear)
    flat = [(100.0, 100.05, 99.95, 100.0) for _ in range(50)]
    return _ict_candles(flat)


def _ict_refine_plan():
    """A 1M series whose tail leaves a small bullish FVG inside the setup-chart gap."""
    rows = []
    prev = 100.53
    for _ in range(57):
        c = prev + 0.01 * math.sin(_ / 3.0)
        rows.append((prev, c + 0.02, c - 0.02, c))
        prev = c
    b = prev
    rows.append((b, b + 0.02, b - 0.05, b - 0.01))          # c1
    rows.append((b - 0.01, b + 0.12, b - 0.02, b + 0.09))   # displacement up
    rows.append((b + 0.04, b + 0.07, b + 0.03, b + 0.045))  # c3 low > c1.high -> FVG
    return rows


class TestICTLiquidity:
    def _signal(self, plan, bias="bull", refine=False):
        spec = preset_specs()["ict_liquidity"]
        higher_tf = _ict_bias_series(bias)
        refine_candles = (
            _ict_candles(_ict_refine_plan(), start_hour=13, step_ms=60_000) if refine else None
        )
        return evaluate_signal(
            "BTC",
            "5m",
            spec,
            _ict_candles(plan),
            higher_tf_candles=higher_tf,
            entry_refine_candles=refine_candles,
        )

    def test_bullish_sweep_mss_fvg_confirms_long(self):
        sig = self._signal(_ict_bullish_plan())
        assert sig.setup_confirmed is True
        assert sig.direction == "LONG"
        assert sig.ict is not None
        assert sig.entry_rules_passed == sig.entry_rules_total == 7

    def test_bearish_sweep_mss_fvg_confirms_short(self):
        sig = self._signal(_ict_bearish_plan(), bias="bear")
        assert sig.setup_confirmed is True
        assert sig.direction == "SHORT"
        assert sig.ict is not None

    def test_uptrend_without_structure_does_not_force_long(self):
        plan = _ict_climb_plan()
        ctx = build_market_context("BTC", "5m", _ict_candles(plan))
        assert ctx.snapshot.trend_bias == "BULLISH"
        sig = self._signal(plan)
        assert sig.setup_confirmed is False
        assert sig.direction == "FLAT"

    def test_bearish_bias_rejects_bullish_setup(self):
        sig = self._signal(_ict_bullish_plan(), bias="bear")
        assert sig.setup_confirmed is False
        assert sig.direction == "FLAT"
        assert any("BEARISH" in r for r in (sig.blocked_reasons or []))

    def test_bullish_bias_rejects_bearish_setup(self):
        sig = self._signal(_ict_bearish_plan(), bias="bull")
        assert sig.setup_confirmed is False
        assert sig.direction == "FLAT"
        assert any("BULLISH" in r for r in (sig.blocked_reasons or []))

    def test_neutral_bias_rejects_both_sides(self):
        sig = self._signal(_ict_bullish_plan(), bias="neutral")
        assert sig.setup_confirmed is False
        assert sig.direction == "FLAT"

    def test_long_plan_levels_ordered(self):
        sig = self._signal(_ict_bullish_plan())
        plan = build_trade_plan(sig, preset_specs()["ict_liquidity"])
        assert plan is not None
        assert plan.direction == "LONG"
        assert plan.entry_type == "LIMIT"
        assert plan.stop_loss < plan.entry_low == plan.entry_high < plan.take_profits[0] < plan.take_profits[1]
        assert plan.risk_reward >= 2.0

    def test_short_plan_levels_ordered(self):
        sig = self._signal(_ict_bearish_plan(), bias="bear")
        plan = build_trade_plan(sig, preset_specs()["ict_liquidity"])
        assert plan is not None
        assert plan.direction == "SHORT"
        assert plan.stop_loss > plan.entry_high >= plan.entry_low > plan.take_profits[0] > plan.take_profits[1]
        assert plan.risk_reward >= 2.0

    def test_higher_tf_gate_and_refine(self):
        sig = self._signal(_ict_bullish_plan(), bias="bull", refine=True)
        assert sig.setup_confirmed is True
        assert sig.direction == "LONG"
        # 1M refinement pulled the CE down from the 5M midpoint (~100.61) into the gap.
        assert 100.50 < sig.ict["ce"] < 100.60
        plan = build_trade_plan(sig, preset_specs()["ict_liquidity"])
        assert plan is not None
        assert plan.entry_type == "LIMIT"
        assert plan.entry_low == plan.entry_high == round(sig.ict["ce"], 4)
        assert 100.50 < plan.entry_low < 100.60