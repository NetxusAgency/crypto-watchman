import logging
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.database.models import NewsAnalysis, NewsArticle, Portfolio
from app.services import db_service
from app.services.news import queries
from app.services.news.analyzer import analyze_asset
from app.services.news.collector import collect_for_symbol, store_articles

logger = logging.getLogger("crypto_watchman.news_service")

SENTIMENT_EMOJI = {"BULLISH": "🟢", "BEARISH": "🔴", "NEUTRAL": "⚪", "MIXED": "🟡"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def refresh_asset_news(session: AsyncSession, symbol: str) -> int:
    """Collect + store + AI-analyze news for one symbol. Returns new article count."""
    symbol = symbol.upper().strip()
    items = await collect_for_symbol(session, symbol)
    added = await store_articles(session, symbol, items)

    stmt = (
        select(NewsArticle)
        .where(
            NewsArticle.related_assets.like(f"%{symbol}%"),
            NewsArticle.expires_at > _utcnow(),
            NewsArticle.processing_status == "pending",
        )
        .order_by(NewsArticle.published_at.desc().nulls_last(), NewsArticle.fetched_at.desc())
        .limit(30)
    )
    rows = (await session.execute(stmt)).scalars().all()
    if rows:
        await analyze_asset(session, symbol, list(rows))
    return added


async def refresh_all(session: AsyncSession, symbols: list[str]) -> int:
    total = 0
    for symbol in symbols:
        try:
            total += await refresh_asset_news(session, symbol)
        except Exception as e:
            logger.warning(f"News refresh failed for {symbol}: {e}")
    return total


async def get_latest_analysis(session: AsyncSession, symbol: str) -> NewsAnalysis | None:
    stmt = (
        select(NewsAnalysis)
        .where(NewsAnalysis.symbol == symbol.upper())
        .order_by(NewsAnalysis.analyzed_at.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_user_symbols(session: AsyncSession, user_id: int) -> list[str]:
    symbols = await db_service.get_portfolio(session, user_id)
    return [p.symbol for p in symbols]


async def cleanup_expired(session: AsyncSession) -> int:
    result = await session.execute(
        delete(NewsArticle).where(NewsArticle.expires_at <= _utcnow())
    )
    await session.commit()
    rows = result.rowcount or 0
    if rows:
        logger.info(f"News cleanup removed {rows} expired articles")
    return rows


def format_analysis(symbol: str, analysis: NewsAnalysis | None) -> str:
    if not analysis:
        return (
            f"📰 <b>BTC</b> news\n\n"
            f"No reliable news gathered for this asset yet. "
            f"Check back after the next scan, or use /digest for a market summary."
        ).replace("BTC", symbol)

    out = [f"📰 <b>{symbol}</b> — Market News"]
    out.append(f"Recent articles analysed: {analysis.article_count}")

    pos = analysis.positive_count or 0
    neg = analysis.negative_count or 0
    neu = analysis.neutral_count or 0
    if pos or neg or neu:
        out.append(
            f"Sentiment split:\n"
            f"🟢 Positive: {pos}\n"
            f"🔴 Negative: {neg}\n"
            f"⚪ Neutral: {neu}"
        )

    sentiment = analysis.overall_sentiment or "NEUTRAL"
    impact = analysis.impact_level or "UNKNOWN"
    direction = analysis.potential_direction or "NEUTRAL"
    conf = analysis.confidence or 0
    score = analysis.impact_score or 0
    out.append(
        f"AI assessment: <b>{SENTIMENT_EMOJI.get(sentiment, '⚪')} {sentiment}</b> · "
        f"Impact {impact} ({score}/100)"
    )
    out.append(f"Potential direction: {direction} · Confidence {conf:.0f}%")

    if analysis.event_categories:
        out.append(f"Key events: {analysis.event_categories}")

    if analysis.reason:
        out.append(f"\nWhy: {analysis.reason}")

    out.append(
        "\n⚠️ AI analysis reflects news mentions only and is not a price prediction."
    )
    return "\n".join(out)


async def build_user_news_report(session: AsyncSession, user_id: int) -> str | None:
    """Collect/refresh then format a report for every portfolio asset."""
    symbols = await get_user_symbols(session, user_id)
    if not symbols:
        return None

    await refresh_all(session, symbols)

    parts: list[str] = []
    for symbol in symbols:
        analysis = await get_latest_analysis(session, symbol)
        parts.append(format_analysis(symbol, analysis) + "\n\n")

    header = f"🧠 <b>AI News Report</b>\nSources: Google News, CoinTelegraph, CoinDesk, Decrypt, The Block, exchanges.\n\n"
    return header + "\n".join(parts).rstrip()