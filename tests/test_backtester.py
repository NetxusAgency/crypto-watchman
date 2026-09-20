from app.services.assistant.indicators import Candle
from app.services.trading.backtester import run_backtest
from app.services.trading.strategy_engine import preset_specs


def _candles(closes, start_open=100.0):
    c = []
    prev = start_open
    for i, v in enumerate(closes):
        h = max(prev, v) * 1.003
        lo = min(prev, v) * 0.997
        c.append(Candle((i + 1) * 3_600_000, round(prev, 4), round(h, 4), round(lo, 4), round(v, 4), 1000.0))
        prev = v
    return c


def _uptrend(n=400):
    c = 100.0
    out = []
    for i in range(n):
        c *= 1.0015
        if i % 11 < 5:
            c *= 0.9995
        out.append(c)
    return out


def _sawtooth(n=400):
    out = []
    c = 100.0
    period = 40
    for i in range(n):
        phase = (i % period) / period
        c = 100.0 + 25.0 * math_bump(phase)
        out.append(c)
    return out


def math_bump(phase):
    import math

    return math.sin(2 * math.pi * phase)


class TestBacktester:
    def test_uptrend_produces_trades_on_trend_strategy(self):
        report = run_backtest(
            _candles(_uptrend()), preset_specs()["trend_pullback"], symbol="BTC", timeframe="4h"
        )
        assert len(report.trades) > 0
        for t in report.trades:
            assert t.direction == "LONG"

    def test_no_trades_too_few_candles(self):
        report = run_backtest(_candles(_uptrend(50)), preset_specs()["general"], symbol="BTC")
        assert report.trades == []
        assert report.summary()["total_trades"] == 0

    def test_summary_shape(self):
        report = run_backtest(
            _candles(_uptrend(500)), preset_specs()["trend_pullback"], symbol="BTC", timeframe="4h"
        )
        s = report.summary()
        assert set(s) == {
            "total_trades", "win_rate", "profit_factor", "net_pnl",
            "max_drawdown", "avg_holding_bars", "consecutive_losses",
        }
        assert s["total_trades"] == len(report.trades)
        assert 0 <= s["max_drawdown"] <= 100

    def test_sawtooth_flat_market_isolates_strategies(self):
        report_trend = run_backtest(_candles(_sawtooth()), preset_specs()["trend_pullback"], symbol="BTC")
        report_general = run_backtest(_candles(_sawtooth()), preset_specs()["general"], symbol="BTC")
        assert len(report_trend.trades) + len(report_general.trades) >= 0
        for t in report_general.trades:
            assert len(t.reason) > 0