import asyncio
import math
from types import SimpleNamespace

from app.services.assistant.analyzer import _extract_json, generate_fallback_setup
from app.services.assistant.assistant_service import format_trade_setup_message
from app.services.assistant.indicators import (
    Candle,
    TechnicalSnapshot,
    calculate_atr,
    calculate_bollinger_bands,
    calculate_ema,
    calculate_macd,
    calculate_rsi,
    calculate_sma,
    calculate_support_resistance,
    compute_all_indicators,
)
from app.services.assistant.strategies import (
    PRESET_STRATEGIES,
    STRATEGIES_MAP,
    get_strategy_definition,
    get_strategy_definition_any,
    is_custom_key,
    strategy_definition_from_row,
)


def _make_dummy_candles(prices: list[float]) -> list[Candle]:
    candles = []
    for i, p in enumerate(prices):
        candles.append(
            Candle(
                timestamp=1700000000 + (i * 3600),
                open=p * 0.998,
                high=p * 1.005,
                low=p * 0.995,
                close=p,
                volume=100.0 + i,
            )
        )
    return candles


class TestIndicators:
    def test_sma(self):
        vals = [10.0, 20.0, 30.0, 40.0, 50.0]
        sma3 = calculate_sma(vals, 3)
        assert sma3[0] is None
        assert sma3[1] is None
        assert sma3[2] == 20.0
        assert sma3[3] == 30.0
        assert sma3[4] == 40.0

    def test_ema(self):
        vals = [10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0]
        ema3 = calculate_ema(vals, 3)
        assert len(ema3) == len(vals)
        assert ema3[0] is None
        assert ema3[1] is None
        assert ema3[2] == 11.0  # Seeded with SMA of [10, 11, 12]
        assert ema3[-1] is not None
        assert ema3[-1] > ema3[2]

    def test_rsi_bullish(self):
        # Monotonically increasing prices
        prices = [float(100 + i * 2) for i in range(25)]
        rsi = calculate_rsi(prices, 14)
        assert rsi is not None
        assert rsi > 80.0

    def test_rsi_bearish(self):
        # Monotonically decreasing prices
        prices = [float(200 - i * 2) for i in range(25)]
        rsi = calculate_rsi(prices, 14)
        assert rsi is not None
        assert rsi < 20.0

    def test_macd(self):
        prices = [float(100 + (i % 5) * 2) for i in range(40)]
        line, signal, hist = calculate_macd(prices, 12, 26, 9)
        assert line is not None
        assert signal is not None
        assert hist is not None
        assert round(line - signal, 4) == round(hist, 4)

    def test_atr(self):
        candles = _make_dummy_candles([100.0 + i for i in range(20)])
        atr = calculate_atr(candles, 14)
        assert atr is not None
        assert atr > 0

    def test_bollinger_bands(self):
        prices = [100.0, 102.0, 101.0, 103.0, 102.0] * 5
        upper, mid, lower = calculate_bollinger_bands(prices, 20)
        assert upper is not None and mid is not None and lower is not None
        assert upper > mid > lower

    def test_support_resistance(self):
        # Create candles with clear swing highs and lows
        prices = [100.0, 105.0, 98.0, 110.0, 95.0, 108.0, 102.0]
        candles = _make_dummy_candles(prices)
        supports, resistances = calculate_support_resistance(candles)
        assert len(supports) > 0
        assert len(resistances) > 0
        assert all(s <= 102.0 for s in supports)
        assert all(r >= 102.0 for r in resistances)

    def test_compute_all_indicators(self):
        prices = [float(50000 + i * 100) for i in range(50)]
        candles = _make_dummy_candles(prices)
        snapshot = compute_all_indicators("BTC", "1h", candles)
        assert snapshot.symbol == "BTC"
        assert snapshot.timeframe == "1h"
        assert snapshot.current_price == 54900.0
        assert snapshot.trend_bias in ("BULLISH", "BEARISH", "NEUTRAL")
        assert snapshot.ema_20 is not None
        assert snapshot.rsi_14 is not None


class TestStrategies:
    def test_preset_strategies_available(self):
        expected_keys = {"trend_pullback", "breakout", "mean_reversion", "general"}
        assert expected_keys.issubset(set(STRATEGIES_MAP.keys()))
        for strat in PRESET_STRATEGIES:
            assert len(strat.rules) > 20
            assert "1h" in strat.timeframes

    def test_get_strategy_fallback(self):
        s = get_strategy_definition("non_existent_key")
        assert s.key == "general"

    def test_is_custom_key(self):
        assert is_custom_key("custom_7")
        assert not is_custom_key("general")
        assert not is_custom_key("")

    def test_strategy_definition_from_row(self):
        row = SimpleNamespace(
            key="custom_7",
            name="Keltner Momentum",
            description="My rules",
            timeframes="15m,1h,4h,1d",
            indicators="EMA,RSI,MACD,ATR",
            rules="Go long when EMA 20 crosses above EMA 50.",
        )
        d = strategy_definition_from_row(row)
        assert d.key == "custom_7"
        assert d.name == "Keltner Momentum"
        assert d.rules == row.rules

    def test_get_definition_any_preset(self):
        d = asyncio.run(get_strategy_definition_any(None, "breakout"))
        assert d.key == "breakout"

    def test_get_definition_any_custom_without_session(self):
        d = asyncio.run(get_strategy_definition_any(None, "custom_999"))
        assert d.key == "general"


class TestAnalyzer:
    def test_extract_json_formats(self):
        # 1. Plain raw JSON
        raw = '{"bias": "BULLISH", "confidence": 80}'
        assert _extract_json(raw) == {"bias": "BULLISH", "confidence": 80}

        # 2. Markdown fenced JSON
        fenced = '```json\n{"bias": "BEARISH", "confidence": 70}\n```'
        assert _extract_json(fenced) == {"bias": "BEARISH", "confidence": 70}

        # 3. Conversational wrapper around JSON
        wrapped = 'Here is the setup:\n{"bias": "NEUTRAL"}\nHope this helps!'
        assert _extract_json(wrapped) == {"bias": "NEUTRAL"}

        # 4. Invalid input
        assert _extract_json("not json at all") is None
        assert _extract_json(None) is None

    def test_fallback_setup_generation_bullish(self):
        snapshot = TechnicalSnapshot(
            symbol="ETH",
            timeframe="1h",
            current_price=3000.0,
            ema_20=2980.0,
            ema_50=2950.0,
            sma_200=2800.0,
            rsi_14=62.0,
            macd_line=15.0,
            macd_signal=10.0,
            macd_hist=5.0,
            atr_14=40.0,
            bb_upper=3100.0,
            bb_middle=2980.0,
            bb_lower=2860.0,
            support_levels=[2950.0],
            resistance_levels=[3150.0],
            trend_bias="BULLISH",
        )
        strategy = get_strategy_definition("trend_pullback")
        setup = generate_fallback_setup(snapshot, strategy)

        assert setup.symbol == "ETH"
        assert setup.bias == "BULLISH"
        assert setup.stop_loss < setup.current_price
        assert setup.take_profit_1 > setup.current_price
        assert setup.take_profit_2 > setup.take_profit_1
        assert setup.risk_reward >= 1.0
        assert len(setup.reasoning) >= 2
        assert "close below" in setup.invalidation

    def test_fallback_setup_generation_bearish(self):
        snapshot = TechnicalSnapshot(
            symbol="SOL",
            timeframe="4h",
            current_price=140.0,
            ema_20=145.0,
            ema_50=150.0,
            sma_200=160.0,
            rsi_14=38.0,
            macd_line=-3.0,
            macd_signal=-1.5,
            macd_hist=-1.5,
            atr_14=5.0,
            bb_upper=155.0,
            bb_middle=145.0,
            bb_lower=135.0,
            support_levels=[130.0],
            resistance_levels=[145.0],
            trend_bias="BEARISH",
        )
        strategy = get_strategy_definition("breakout")
        setup = generate_fallback_setup(snapshot, strategy)

        assert setup.symbol == "SOL"
        assert setup.bias == "BEARISH"
        assert setup.stop_loss > setup.current_price
        assert setup.take_profit_1 < setup.current_price


class TestFormatting:
    def test_format_trade_setup_message(self):
        snapshot = TechnicalSnapshot(
            symbol="BTC",
            timeframe="1h",
            current_price=64000.0,
            ema_20=63800.0,
            ema_50=63500.0,
            sma_200=60000.0,
            rsi_14=58.5,
            macd_line=25.0,
            macd_signal=15.0,
            macd_hist=10.0,
            atr_14=600.0,
            bb_upper=65000.0,
            bb_middle=63800.0,
            bb_lower=62600.0,
            support_levels=[63200.0],
            resistance_levels=[65500.0],
            trend_bias="BULLISH",
        )
        strategy = get_strategy_definition("general")
        setup = generate_fallback_setup(snapshot, strategy)
        formatted = format_trade_setup_message(setup)

        assert "AI Trading Assistant — BTC (1H)" in formatted
        assert "Entry Zone:" in formatted
        assert "Stop Loss:" in formatted
        assert "Take Profit 1:" in formatted
        assert "Risk/Reward Ratio:" in formatted
        assert "RSI (14):" in formatted
        assert "Invalidation Rule:" in formatted
        assert "Strictly for analysis and educational reference" in formatted

    def test_format_escapes_unsafe_html(self):
        snapshot = TechnicalSnapshot(
            symbol="BTC",
            timeframe="1h",
            current_price=64000.0,
            ema_20=63800.0,
            ema_50=63500.0,
            sma_200=60000.0,
            rsi_14=58.5,
            macd_line=25.0,
            macd_signal=15.0,
            macd_hist=10.0,
            atr_14=600.0,
            bb_upper=65000.0,
            bb_middle=63800.0,
            bb_lower=62600.0,
            support_levels=[63200.0],
            resistance_levels=[65500.0],
            trend_bias="BULLISH",
        )
        setup = generate_fallback_setup(snapshot, get_strategy_definition("general"))
        setup.reasoning = ["EMA 20 (76173) < EMA 50 (76746)"]
        setup.invalidation = "Close > 77000 invalidates <b>short</b>"

        formatted = format_trade_setup_message(setup)

        assert "&lt;" in formatted
        assert "&gt;" in formatted
        assert "EMA 20 (76173) < EMA 50" not in formatted
        assert "invalidates <b>short</b>" not in formatted
