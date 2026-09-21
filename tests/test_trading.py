import math
import random

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