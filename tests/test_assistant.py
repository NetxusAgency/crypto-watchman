import asyncio
import math
from types import SimpleNamespace

from app.services.assistant.analyzer import (
    _extract_json,
    evaluate_entry,
    evaluate_entry_from_data,
    generate_fallback_setup,
)
from app.services.assistant.assistant_service import format_trade_setup_message
from app.services.assistant.document_parser import (
    extract_document_extension,
    extract_text_from_document,
)
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
    _extract_json_array,
    _fallback_document_split,
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

    def test_format_no_entry_card(self):
        snapshot = TechnicalSnapshot(
            symbol="BTC",
            timeframe="1h",
            current_price=64000.0,
            ema_20=63800.0,
            ema_50=63500.0,
            sma_200=60000.0,
            rsi_14=25.0,
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
        setup = generate_fallback_setup(snapshot, get_strategy_definition("trend_pullback"))
        setup.can_enter = False
        setup.entry_reason = "RSI is too overextended to chase here."

        formatted = format_trade_setup_message(setup)

        assert "Market conditions do NOT favour entry" in formatted
        assert "Entry Zone:" not in formatted
        assert "Stop Loss:" not in formatted
        assert "Why no entry:" in formatted

    def test_format_conditions_favour_entry(self):
        snapshot = TechnicalSnapshot(
            symbol="BTC",
            timeframe="1h",
            current_price=64000.0,
            ema_20=63800.0,
            ema_50=63500.0,
            sma_200=60000.0,
            rsi_14=55.0,
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
        setup = generate_fallback_setup(snapshot, get_strategy_definition("trend_pullback"))

        formatted = format_trade_setup_message(setup)

        assert setup.can_enter is True
        assert "Conditions favour entry" in formatted
        assert "Entry Zone:" in formatted


class TestEntryVerdict:
    def _snapshot(self, rsi=None, trend="BULLISH", bb_upper=100.0, bb_lower=96.0, macd=1.0):
        return TechnicalSnapshot(
            symbol="BTC",
            timeframe="1h",
            current_price=98.0,
            ema_20=97.0,
            ema_50=95.0,
            sma_200=90.0,
            rsi_14=rsi,
            macd_line=2.0,
            macd_signal=1.0,
            macd_hist=macd,
            atr_14=2.0,
            bb_upper=bb_upper,
            bb_middle=98.0,
            bb_lower=bb_lower,
            support_levels=[95.0],
            resistance_levels=[102.0],
            trend_bias=trend,
        )

    def test_mean_reversion_oversold_favours(self):
        can_enter, reason = evaluate_entry("mean_reversion", self._snapshot(rsi=25.0))
        assert can_enter is True
        assert "oversold" in reason.lower()

    def test_mean_reversion_neutral_rsi_blocks(self):
        can_enter, reason = evaluate_entry("mean_reversion", self._snapshot(rsi=52.0, trend="NEUTRAL"))
        assert can_enter is False
        assert "not in the extreme zone" in reason.lower()

    def test_breakout_squeeze_favours(self):
        can_enter, reason = evaluate_entry("breakout", self._snapshot(trend="NEUTRAL", bb_upper=100.0, bb_lower=97.0))
        assert can_enter is True

    def test_breakout_no_signal_blocks(self):
        can_enter, reason = evaluate_entry("breakout", self._snapshot(trend="NEUTRAL", bb_upper=130.0, bb_lower=66.0, macd=0.0))
        assert can_enter is False

    def test_general_neutral_blocks(self):
        can_enter, _ = evaluate_entry("general", self._snapshot(rsi=50.0, trend="NEUTRAL"))
        assert can_enter is False

    def test_custom_trend_overextended_blocks(self):
        can_enter, _ = evaluate_entry("custom_1", self._snapshot(rsi=25.0))
        assert can_enter is False

    def test_custom_trend_favours(self):
        can_enter, reason = evaluate_entry("custom_1", self._snapshot(rsi=55.0))
        assert can_enter is True
        assert "entry" in reason.lower()

    def test_evaluate_from_dict_matches(self):
        snapshot = self._snapshot(rsi=55.0)
        data = snapshot.to_dict()
        can_enter, reason = evaluate_entry_from_data("custom_1", data)
        assert can_enter is True
        assert "entry" in reason.lower()


class TestDocumentImport:
    def test_extract_document_extension(self):
        assert extract_document_extension("trading.pdf") == ".pdf"
        assert extract_document_extension("strategy.DOCX") == ".docx"
        assert extract_document_extension("notes") == ""

    def test_extract_text_txt(self):
        text = extract_text_from_document(b"Strategy One rules here\nEntry when RSI < 30.", "notes.txt")
        assert "Strategy One" in text
        assert "RSI" in text

    def test_fallback_split_multiple_strategies(self):
        doc = (
            "Strategy: Momentum Breakout\n"
            "Enter when price breaks the 20-period high with volume. Stop below the low of the breakout bar.\n"
            "\n"
            "Strategy: Mean Reversion\n"
            "Buy when RSI dips below 30 near support. Target the middle band. Stop below the swing low.\n"
        )
        items = _fallback_document_split(doc)
        assert len(items) == 2
        assert items[0]["name"] == "Momentum Breakout"
        assert items[1]["name"] == "Mean Reversion"
        assert len(items[0]["rules"]) > 20

    def test_fallback_single_strategy_when_no_headings(self):
        items = _fallback_document_split("Buy BTC when RSI is under 30 on the 4h chart and price is above the EMA20.")
        assert len(items) == 1
        assert items[0]["name"] == "Imported Strategy"
        assert "RSI" in items[0]["rules"]

    def test_extract_json_array(self):
        raw = '```json\n{"strategies": [{"name": "A", "rules": "x"}, {"name": "B", "rules": "y"}]}\n```'
        arr = _extract_json_array(raw)
        assert arr is not None
        assert len(arr) == 2
        assert arr[0]["name"] == "A"

    def test_extract_json_array_wrapper_keys(self):
        raw = '{"trading_strategies": [{"name": "A", "rules": "x"}]}'
        arr = _extract_json_array(raw)
        assert arr is not None
        assert arr[0]["name"] == "A"

    def test_extract_json_array_raw_top_level(self):
        raw = '[{"name": "X", "rules": "z"}, {"name": "Y", "rules": "w"}]'
        arr = _extract_json_array(raw)
        assert arr is not None
        assert len(arr) == 2

    def test_extract_json_array_invalid(self):
        assert _extract_json_array("not json") is None
        assert _extract_json_array(None) is None

    def test_fallback_split_numbered_headings(self):
        doc = (
            "1. Momentum Breakout\n"
            "Enter on a break of the 20-period high with heavy volume. Stop below the breakout bar low.\n"
            "\n"
            "2. Mean Reversion\n"
            "Buy when RSI dips under 30 near support. Target the middle band. Stop below the swing low.\n"
        )
        items = _fallback_document_split(doc)
        assert len(items) == 2
        assert items[0]["name"] == "Momentum Breakout"
        assert items[1]["name"] == "Mean Reversion"
