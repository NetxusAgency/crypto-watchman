import json
import logging
import re
from dataclasses import asdict, dataclass
from typing import Sequence

from app.services.assistant.indicators import Candle, TechnicalSnapshot
from app.services.assistant.strategies import StrategyDefinition
from app.services.market_digest.digest_service import call_llm

logger = logging.getLogger("crypto_watchman.assistant_analyzer")

SYSTEM_ASSISTANT_PROMPT = (
    "You are an elite quantitative technical analyst and trade setup assistant. "
    "You evaluate real market indicators and price action to generate disciplined, risk-managed trade setups. "
    "Rules: "
    "1. Never promise guaranteed profits or outcomes. State clear technical invalidation levels. "
    "2. Base your plan strictly on the supplied indicators, support/resistance levels, and strategy guidelines. "
    "3. Respond ONLY with valid, unformatted JSON matching the required schema."
)


@dataclass
class TradeSetup:
    symbol: str
    timeframe: str
    strategy_key: str
    strategy_name: str
    bias: str  # BULLISH, BEARISH, NEUTRAL
    current_price: float
    entry_min: float
    entry_max: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    risk_reward: float
    confidence: int  # 0-100
    indicators_summary: dict
    reasoning: list[str]
    invalidation: str

    def to_dict(self) -> dict:
        return asdict(self)


def _extract_json(text: str | None) -> dict | None:
    if not text:
        return None
    cleaned = re.sub(r"```(?:json)?", "", text).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        return json.loads(cleaned[start : end + 1])
    except Exception:
        return None


def generate_fallback_setup(
    snapshot: TechnicalSnapshot, strategy: StrategyDefinition
) -> TradeSetup:
    """Generate a mathematically sound trade setup when LLM is unavailable or unconfigured."""
    p = snapshot.current_price
    atr = snapshot.atr_14 or (p * 0.02)
    bias = snapshot.trend_bias

    supports = snapshot.support_levels or [p * 0.97]
    resistances = snapshot.resistance_levels or [p * 1.03]

    if bias == "BULLISH":
        entry_min = round(min(supports[0], p * 0.995), 4)
        entry_max = round(p, 4)
        # SL below closest support or 1.5x ATR
        stop_loss = round(min(supports[0] - (0.5 * atr), p - (1.5 * atr)), 4)
        risk = max(entry_max - stop_loss, 0.0001)
        take_profit_1 = round(resistances[0] if resistances[0] > entry_max else entry_max + (1.5 * risk), 4)
        take_profit_2 = round(take_profit_1 + (1.0 * risk), 4)
        rr = round((take_profit_1 - entry_max) / risk, 2)
        reasoning = [
            f"Price trading above key dynamic levels with {snapshot.trend_bias.lower()} alignment.",
            f"RSI at {snapshot.rsi_14 or 'neutral'} showing sustained buying structure.",
            f"Favorable risk positioning above local support at ${supports[0]:,.4f}.",
        ]
        invalidation = f"4-hour close below ${stop_loss:,.4f} invalidates bullish thesis."
    elif bias == "BEARISH":
        entry_min = round(p, 4)
        entry_max = round(max(resistances[0], p * 1.005), 4)
        stop_loss = round(max(resistances[0] + (0.5 * atr), p + (1.5 * atr)), 4)
        risk = max(stop_loss - entry_min, 0.0001)
        take_profit_1 = round(supports[0] if supports[0] < entry_min else entry_min - (1.5 * risk), 4)
        take_profit_2 = round(take_profit_1 - (1.0 * risk), 4)
        rr = round((entry_min - take_profit_1) / risk, 2)
        reasoning = [
            f"Downward momentum evident across moving averages and MACD histogram.",
            f"RSI at {snapshot.rsi_14 or 'neutral'} reflecting selling pressure.",
            f"Risk capped below nearest resistance at ${resistances[0]:,.4f}.",
        ]
        invalidation = f"Candle close above ${stop_loss:,.4f} breaks bearish continuation."
    else:  # NEUTRAL / RANGE
        entry_min = round(supports[0], 4)
        entry_max = round(p, 4)
        stop_loss = round(supports[0] - (1.0 * atr), 4)
        risk = max(entry_max - stop_loss, 0.0001)
        take_profit_1 = round(resistances[0], 4)
        take_profit_2 = round(resistances[0] + (1.0 * atr), 4)
        rr = round(max((take_profit_1 - entry_max) / risk, 1.2), 2)
        reasoning = [
            "Market is consolidating in a horizontal equilibrium band.",
            f"Range bounds established between ${supports[0]:,.4f} and ${resistances[0]:,.4f}.",
            "Strategy favors patient limit orders near range boundaries.",
        ]
        invalidation = f"Breakout and hold outside ${supports[0]:,.4f} - ${resistances[0]:,.4f} range."

    return TradeSetup(
        symbol=snapshot.symbol,
        timeframe=snapshot.timeframe,
        strategy_key=strategy.key,
        strategy_name=strategy.name,
        bias=bias,
        current_price=p,
        entry_min=entry_min,
        entry_max=entry_max,
        stop_loss=stop_loss,
        take_profit_1=take_profit_1,
        take_profit_2=take_profit_2,
        risk_reward=max(rr, 1.0),
        confidence=65 if bias != "NEUTRAL" else 50,
        indicators_summary=snapshot.to_dict(),
        reasoning=reasoning,
        invalidation=invalidation,
    )


async def analyze_market_setup(
    snapshot: TechnicalSnapshot,
    strategy: StrategyDefinition,
    candles: Sequence[Candle],
) -> TradeSetup:
    """Evaluate technical snapshot and candles with AI and return structured TradeSetup."""
    recent_closes = [c.close for c in candles[-5:]] if candles else []

    prompt = f"""Evaluate this trading setup for {snapshot.symbol} on the {snapshot.timeframe} timeframe.

Strategy: {strategy.name}
Strategy Rules: {strategy.rules}

Technical Indicators Snapshot:
- Current Price: ${snapshot.current_price}
- EMA 20: {snapshot.ema_20} | EMA 50: {snapshot.ema_50} | SMA 200: {snapshot.sma_200}
- RSI (14): {snapshot.rsi_14}
- MACD Line: {snapshot.macd_line} | Signal: {snapshot.macd_signal} | Histogram: {snapshot.macd_hist}
- ATR (14): {snapshot.atr_14}
- Bollinger Bands: Upper={snapshot.bb_upper}, Middle={snapshot.bb_middle}, Lower={snapshot.bb_lower}
- Key Supports: {snapshot.support_levels}
- Key Resistances: {snapshot.resistance_levels}
- Computed Trend Bias: {snapshot.trend_bias}
- Recent Closes: {recent_closes}

Produce a structured, professional trade setup.
Respond strictly in JSON format matching this schema:
{{
  "bias": "BULLISH" or "BEARISH" or "NEUTRAL",
  "entry_min": float,
  "entry_max": float,
  "stop_loss": float,
  "take_profit_1": float,
  "take_profit_2": float,
  "risk_reward": float,
  "confidence": int (0-100),
  "reasoning": ["point 1", "point 2", "point 3"],
  "invalidation": "string describing condition that cancels setup"
}}"""

    try:
        raw_response = await call_llm(
            prompt,
            max_tokens=700,
            system_prompt=SYSTEM_ASSISTANT_PROMPT,
        )
        parsed = _extract_json(raw_response)

        if parsed and "bias" in parsed and "stop_loss" in parsed:
            bias_val = str(parsed.get("bias", snapshot.trend_bias)).upper()
            if bias_val not in ("BULLISH", "BEARISH", "NEUTRAL"):
                bias_val = snapshot.trend_bias

            entry_min = float(parsed.get("entry_min", snapshot.current_price))
            entry_max = float(parsed.get("entry_max", snapshot.current_price))
            stop_loss = float(parsed["stop_loss"])
            tp1 = float(parsed.get("take_profit_1", snapshot.current_price * 1.03))
            tp2 = float(parsed.get("take_profit_2", snapshot.current_price * 1.06))
            rr = float(parsed.get("risk_reward", 1.5))
            conf = int(parsed.get("confidence", 65))
            reasons = list(parsed.get("reasoning", [])) or ["Technical confluence across multi-indicator set."]
            inval = str(parsed.get("invalidation", f"Break of ${stop_loss:,.4f} cancels setup."))

            return TradeSetup(
                symbol=snapshot.symbol,
                timeframe=snapshot.timeframe,
                strategy_key=strategy.key,
                strategy_name=strategy.name,
                bias=bias_val,
                current_price=snapshot.current_price,
                entry_min=entry_min,
                entry_max=entry_max,
                stop_loss=stop_loss,
                take_profit_1=tp1,
                take_profit_2=tp2,
                risk_reward=rr,
                confidence=min(max(conf, 10), 95),
                indicators_summary=snapshot.to_dict(),
                reasoning=reasons,
                invalidation=inval,
            )
    except Exception as e:
        logger.warning(f"LLM assistant evaluation failed, using fallback setup: {e}")

    return generate_fallback_setup(snapshot, strategy)
