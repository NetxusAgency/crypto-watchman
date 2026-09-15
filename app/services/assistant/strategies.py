from dataclasses import dataclass
from typing import Sequence
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database.models import TradingStrategy


@dataclass
class StrategyDefinition:
    key: str
    name: str
    description: str
    timeframes: str
    indicators: str
    rules: str


PRESET_STRATEGIES: list[StrategyDefinition] = [
    StrategyDefinition(
        key="trend_pullback",
        name="Trend Following / Pullback",
        description="Identifies continuation entries when price pulls back to key moving averages in an established trend.",
        timeframes="15m,1h,4h,1d",
        indicators="EMA20,EMA50,RSI14,ATR14",
        rules=(
            "Trend alignment requires EMA 20 > EMA 50 for longs, or EMA 20 < EMA 50 for shorts. "
            "Enter on pullbacks to the dynamic EMA 20/50 support/resistance zone when RSI normalizes. "
            "Stop loss is positioned beyond the recent swing low/high or 1.5x ATR. "
            "Take profit targets are set at recent swing highs/lows and next resistance/support levels."
        ),
    ),
    StrategyDefinition(
        key="breakout",
        name="Breakout & Momentum",
        description="Captures explosive moves when price breaches established horizontal support or resistance levels.",
        timeframes="15m,1h,4h,1d",
        indicators="Resistance,Support,MACD,ATR14",
        rules=(
            "Identify consolidation ranges where price tests key resistance (bullish) or support (bearish). "
            "Entry triggers on confirmed break or retest of the broken level with MACD histogram confirmation. "
            "Stop loss placed safely inside the broken consolidation zone. "
            "Targets set at measured-move extensions (1.5x and 2.5x the consolidation height)."
        ),
    ),
    StrategyDefinition(
        key="mean_reversion",
        name="Mean Reversion / Counter-Trend",
        description="Exploits market extremes when price is stretched outside Bollinger Bands with divergent RSI.",
        timeframes="15m,1h,4h,1d",
        indicators="BollingerBands,RSI14,SMA20",
        rules=(
            "Triggers when RSI < 30 (oversold) and price touches/pierces Lower Bollinger Band for longs, "
            "or RSI > 70 (overbought) and price touches Upper Bollinger Band for shorts. "
            "Target 1 is the 20-period middle moving average; Target 2 is the opposite band. "
            "Stop loss placed strictly beyond the extreme candle wick."
        ),
    ),
    StrategyDefinition(
        key="general",
        name="Full Technical Diagnostic",
        description="Comprehensive technical analysis synthesizing trend, momentum, support/resistance, and risk parameters.",
        timeframes="15m,1h,4h,1d",
        indicators="EMA,RSI,MACD,ATR,BollingerBands,Support,Resistance",
        rules=(
            "Synthesize all indicators (EMA 20/50, RSI, MACD histogram, ATR, Support & Resistance). "
            "Determine the dominant market structure and produce an actionable trade setup with clear "
            "Entry Zone, Stop Loss, TP1, TP2, and calculated Risk:Reward ratio."
        ),
    ),
]

STRATEGIES_MAP = {s.key: s for s in PRESET_STRATEGIES}


async def seed_preset_strategies(session: AsyncSession) -> int:
    """Ensure system preset strategies exist in the database."""
    created = 0
    for s in PRESET_STRATEGIES:
        stmt = select(TradingStrategy).where(
            TradingStrategy.key == s.key,
            TradingStrategy.is_system == True,
        )
        result = await session.execute(stmt)
        existing = result.scalar_one_or_none()
        if not existing:
            strategy = TradingStrategy(
                user_id=None,
                key=s.key,
                name=s.name,
                description=s.description,
                timeframes=s.timeframes,
                indicators=s.indicators,
                rules=s.rules,
                is_system=True,
                version=1,
            )
            session.add(strategy)
            created += 1
    if created:
        await session.commit()
    return created


def get_strategy_definition(key: str) -> StrategyDefinition:
    """Retrieve strategy definition by key, falling back to general diagnostic."""
    return STRATEGIES_MAP.get(key, STRATEGIES_MAP["general"])
