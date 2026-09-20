import math

from app.services.assistant.indicators import Candle
from app.services.trading import (
    format_close_message,
    format_paper_card,
)
from app.services.trading.monitor import TradingMonitor
from app.services.trading.trade_plan import TradePlan


def _candles(closes):
    candles = []
    prev = closes[0]
    for i, v in enumerate(closes):
        h = max(prev, v) * 1.003
        lo = min(prev, v) * 0.997
        candles.append(Candle((i + 1) * 3_600_000, round(prev, 4), round(h, 4), round(lo, 4), round(v, 4), 1000.0))
        prev = v
    return candles


def _bullish(n=160):
    c = 100.0
    out = []
    for i in range(n):
        c *= 1.0022
        if i % 9 < 4:
            c *= 0.999
        out.append(c)
    return out


def _flat(n=120):
    return [100.0] * n


class TestMonitorFormatters:
    def test_paper_card_includes_levels(self):
        plan = TradePlan(
            plan_id="TP-X", symbol="BTC", direction="LONG", timeframe="4h",
            strategy_key="trend_pullback", strategy_name="Trend",
            entry_low=99.5, entry_high=100.5, stop_loss=96.0,
            take_profits=[108.0, 112.0], invalidation="Close below 96 invalidates.",
            risk_reward=1.7,
        )
        card = format_paper_card(plan)
        assert "BTC" in card
        assert "LONG" in card
        assert "99.50" in card and "100.50" in card
        assert "96.00" in card

    def test_close_message_signs(self):
        win = format_close_message("ETH", "TP1", 50.0, 2000.0)
        loss = format_close_message("ETH", "STOP", -12.5, 1900.0)
        assert "+50.00" in win
        assert "-12.50" in loss
        assert "🎯" in win and "🛑" in loss


class TestMonitorEvaluation:
    def test_presets_find_setups_on_bullish_data(self):
        monitor = TradingMonitor()
        proposals = monitor.evaluate_presets("BTC", _candles(_bullish()))
        assert proposals
        for p in proposals:
            assert p.plan is not None
            assert p.state == "setup"
            assert p.plan.to_dict()["paper_only"] is True

    def test_presets_empty_on_flat_data(self):
        monitor = TradingMonitor()
        proposals = monitor.evaluate_presets("BTC", _candles(_flat()))
        assert not proposals