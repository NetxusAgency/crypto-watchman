import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import ListingEvent, NewsAnalysis, WhaleTransaction, Asset
from app.services.assistant.indicators import compute_all_indicators
from app.services.assistant.klines import kline_fetcher
from app.services.sentiment.sentiment_monitor import sentiment_monitor
from app.services.whale_tracker.assets import get_assets_map

logger = logging.getLogger("crypto_watchman.opportunity_signals")

COMPONENT_WEIGHTS: dict[str, int] = {
    "news": 20,
    "volume": 20,
    "momentum": 15,
    "social": 15,
    "listing": 15,
    "whale": 10,
    "liquidity": 5,
}


@dataclass
class OpportunityComponent:
    source: str
    direction: int
    strength: float
    evidence: str

    @property
    def weight(self) -> int:
        return COMPONENT_WEIGHTS.get(self.source, 0)

    @property
    def score(self) -> float:
        return round(self.weight * self.strength * self.direction, 2)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


async def news_signal(session: AsyncSession, symbol: str) -> OpportunityComponent:
    stmt = (
        select(NewsAnalysis)
        .where(NewsAnalysis.symbol == symbol.upper())
        .order_by(NewsAnalysis.analyzed_at.desc())
        .limit(1)
    )
    analysis = (await session.execute(stmt)).scalar_one_or_none()
    if not analysis:
        return OpportunityComponent("news", 0, 0.0, "No recent AI news analysis.")
    sentiment = (analysis.overall_sentiment or "NEUTRAL").upper()
    direction = 1 if sentiment == "BULLISH" else (-1 if sentiment == "BEARISH" else 0)
    impact = _clamp((analysis.impact_score or 0) / 100.0)
    confidence = _clamp((analysis.confidence or 0) / 100.0)
    strength = impact * (0.5 + 0.5 * confidence)
    evidence = (
        f"Sentiment {sentiment}, impact {analysis.impact_score}/100, "
        f"confidence {round((analysis.confidence or 0))}%, {analysis.potential_direction or 'N/A'}"
    )
    return OpportunityComponent("news", direction, round(strength, 3), evidence)


async def market_signal(symbol: str) -> tuple[OpportunityComponent, OpportunityComponent]:
    """Volume + momentum from a single 4h candle fetch (deterministic)."""
    candles = await kline_fetcher.fetch_candles(symbol, "4h", 60)
    if not candles or len(candles) < 24:
        return (
            OpportunityComponent("volume", 0, 0.0, "No recent OHLCV data."),
            OpportunityComponent("momentum", 0, 0.0, "No recent OHLCV data."),
        )

    # Volume: last 24h (6 x 4h) vs prior 24h average.
    last24 = sum(c.volume for c in candles[-6:])
    prev24 = sum(c.volume for c in candles[-12:-6])
    avg_prev = prev24 / 6.0
    ratio = (last24 / avg_prev) if avg_prev > 0 else 0.0
    vol_strength = _clamp((ratio - 1.0) / 2.0)

    # Direction of the move the volume is confirming: 24h price change.
    current = candles[-1].close
    prior = candles[-7].close if len(candles) >= 7 else candles[0].close
    pct_24h = ((current - prior) / prior * 100) if prior else 0.0
    vol_direction = 1 if pct_24h > 0.3 else (-1 if pct_24h < -0.3 else 0)
    vol_evidence = (
        f"24h vol {last24:,.0f} vs ~{avg_prev:,.0f} ({ratio:.2f}x), 24h change {pct_24h:+.2f}%"
    )

    # Momentum: indicator snapshot on the same candles.
    snap = compute_all_indicators(symbol, "4h", candles)
    votes = 0.0
    if snap.ema_20 and snap.ema_50:
        votes += 1 if snap.ema_20 > snap.ema_50 else -1
    if snap.ema_20:
        votes += 1 if current > snap.ema_20 else -1
    if snap.rsi_14 is not None:
        votes += 1 if snap.rsi_14 > 55 else (-1 if snap.rsi_14 < 45 else 0)
    if snap.macd_hist is not None:
        votes += 1 if snap.macd_hist > 0 else (-1 if snap.macd_hist < 0 else 0)
    mom_direction = 1 if votes > 0 else (-1 if votes < 0 else 0)
    mom_strength = _clamp(abs(votes) / 4.0, 0.15, 1.0) if mom_direction else 0.0
    mom_evidence = (
        f"{snap.trend_bias}, RSI {snap.rsi_14}, MACD hist {snap.macd_hist}, "
        f"price {'>' if snap.ema_20 and current > snap.ema_20 else '<'} EMA20 ({snap.ema_20})"
    )
    return (
        OpportunityComponent("volume", vol_direction, round(vol_strength, 3), vol_evidence),
        OpportunityComponent("momentum", mom_direction, round(mom_strength, 3), mom_evidence),
    )


async def social_signal(symbol: str) -> OpportunityComponent:
    try:
        mentions = await sentiment_monitor.get_mention_count(symbol)
        total = int(mentions.get("total", 0))
    except Exception as e:
        logger.debug(f"Social signal failed for {symbol}: {e}")
        total = 0
    strength = _clamp(total / 20.0)
    direction = 1 if total >= 3 else 0
    evidence = f"{total} mention(s) on Reddit / CoinTelegraph / CoinDesk (24h)"
    return OpportunityComponent("social", direction, round(strength, 3), evidence)


async def listing_signal(session: AsyncSession, symbol: str) -> OpportunityComponent:
    from datetime import timedelta
    since = _utcnow() - timedelta(hours=24)
    rows = (
        await session.execute(
            select(ListingEvent.exchange, ListingEvent.pair)
            .where(ListingEvent.symbol == symbol.upper(), ListingEvent.created_at >= since)
            .order_by(ListingEvent.created_at.desc())
        )
    ).all()
    if not rows:
        return OpportunityComponent("listing", 0, 0.0, "No new exchange listings in last 24h.")
    exchanges = ", ".join(f"{r.exchange}" for r in rows[:3])
    strength = _clamp(len(rows) / 2.0)
    return OpportunityComponent(
        "listing", 1, round(strength, 3),
        f"Listed on {exchanges} within 24h",
    )


async def whale_signal(session: AsyncSession, symbol: str) -> OpportunityComponent:
    from datetime import timedelta
    since = _utcnow() - timedelta(hours=24)
    rows = (
        await session.execute(
            select(WhaleTransaction.direction, func.coalesce(WhaleTransaction.value_usd, 0))
            .where(WhaleTransaction.asset == symbol.upper(), WhaleTransaction.created_at >= since)
        )
    ).all()
    if not rows:
        return OpportunityComponent("whale", 0, 0.0, "No whale activity in last 24h.")
    bought = sum(v for d, v in rows if d == "BUY")
    sold = sum(v for d, v in rows if d == "SELL")
    net = bought - sold
    if abs(net) < 1e-9 and bought == 0 and sold == 0:
        return OpportunityComponent("whale", 0, 0.0, "Whale flows neutral in last 24h.")
    direction = 1 if net > 0 else (-1 if net < 0 else 0)
    strength = _clamp(abs(net) / 10_000_000.0, 0.1, 1.0)
    evidence = f"Whale inflow ≈ ${bought:,.0f} vs outflow ≈ ${sold:,.0f} (24h)"
    return OpportunityComponent("whale", direction, round(strength, 3), evidence)


async def liquidity_signal(session: AsyncSession, symbol: str) -> OpportunityComponent:
    assets_map = await get_assets_map(session)
    asset = assets_map.get(symbol.upper())
    if not asset:
        return OpportunityComponent("liquidity", 0, 0.0, "Not in asset registry.")
    if asset.flags and "stablecoin" in asset.flags:
        return OpportunityComponent("liquidity", 0, 0.0, "Stablecoin (excluded).")
    strength = {"high": 1.0, "medium": 0.6, "low": 0.25}.get(asset.tier, 0.0)
    return OpportunityComponent(
        "liquidity", 0, round(strength, 3), f"Registry liquidity tier: {asset.tier}"
    )


async def collect_signals(session: AsyncSession, symbol: str) -> list[OpportunityComponent]:
    volume_c, momentum_c = await market_signal(symbol)
    components = [
        await news_signal(session, symbol),
        volume_c,
        momentum_c,
        await social_signal(symbol),
        await listing_signal(session, symbol),
        await whale_signal(session, symbol),
        await liquidity_signal(session, symbol),
    ]
    return components