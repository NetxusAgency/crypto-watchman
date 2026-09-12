import json
import logging
import re
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import NewsAnalysis, NewsArticle
from app.services.market_digest.digest_service import call_llm

logger = logging.getLogger("crypto_watchman.news_analyzer")

ANALYSIS_PROMPT = """You are a crypto market intelligence analyst. Analyse the news headlines below for {symbol}.

Rules:
- Classify each headline as Bullish, Bearish, or Neutral based ONLY on the headline/context given.
- overall_sentiment must be one of BULLISH / BEARISH / NEUTRAL / MIXED.
- impact_level must be LOW, MEDIUM or HIGH; impact_score is 0-100.
- potential_direction must be UPWARD, DOWNWARD or NEUTRAL.
- confidence is 0-100.
- State uncertainty. Never predict guaranteed prices.
- event_categories: short labels like "Exchange Listing", "Regulation", "Hack", "Upgrade", "Partnership", "ETF", other.

Headlines ({count}):

{headlines}

Respond ONLY with raw JSON (no markdown fences) matching exactly:
{{"articles":[{{"title":"...","sentiment":"Bullish"}}],
"overall_sentiment":"...","positive_count":0,"negative_count":0,"neutral_count":0,
"impact_level":"...","impact_score":0,"potential_direction":"...",
"confidence":0,"time_horizon":"...","event_categories":["..."],"reason":"..."}}"""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


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


def _coerce_sentiment(value: str) -> str | None:
    v = (value or "").strip().upper()
    if v in {"BULLISH", "BEARISH", "NEUTRAL"}:
        return v
    if v in {"BULL", "BULLISH/BEARISH"}:
        return "NEUTRAL"
    return None


async def analyze_asset(session: AsyncSession, symbol: str, articles: list[NewsArticle]) -> NewsAnalysis | None:
    """Run one LLM call for a symbol; store per-article + aggregate analysis."""
    if not articles:
        return None
    now = _utcnow()
    lines = "\n".join(f'{i+1}. "{a.title[:150]}" [{a.source}]' for i, a in enumerate(articles[:25]))
    prompt = ANALYSIS_PROMPT.format(symbol=symbol.upper(), count=min(len(articles), 25), headlines=lines)

    text = await call_llm(prompt, max_tokens=1200)
    data = _extract_json(text) if text else None
    if not data:
        logger.warning(f"No usable LLM output for {symbol}")
        return None

    article_map = {}
    for row in data.get("articles") or []:
        title = (row.get("title") or "").strip().lower()
        if title:
            article_map[title] = row

    stock = _coerce_sentiment(data.get("overall_sentiment") or "")
    if not stock:
        stock = "NEUTRAL"

    positives, negatives, neutrals = 0, 0, 0
    for row in data.get("articles") or []:
        s = _coerce_sentiment(row.get("sentiment") or "")
        if s == "BULLISH":
            positives += 1
        elif s == "BEARISH":
            negatives += 1
        else:
            neutrals += 1

    # Write per-article sentiment back to DB (match by normalized title).
    for art in articles:
        key = (art.title or "").strip().lower()[:100]
        row = article_map.get(key) or article_map.get(key[:80])
        s = _coerce_sentiment(row.get("sentiment") or "") if row else None
        art.sentiment = s or stock
        art.processing_status = "analyzed"

    analysis = NewsAnalysis(
        symbol=symbol.upper(),
        article_count=len(articles),
        positive_count=positives,
        negative_count=negatives,
        neutral_count=neutrals,
        overall_sentiment=stock,
        impact_level=((data.get("impact_level") or "").upper()[:20]) or None,
        impact_score=int(data.get("impact_score") or 0),
        potential_direction=((data.get("potential_direction") or "").upper()[:20]) or None,
        confidence=float(data.get("confidence") or 0),
        time_horizon=(data.get("time_horizon") or "")[:50] or None,
        event_categories=", ".join(data.get("event_categories") or [])[:500] or None,
        reason=(data.get("reason") or "")[:3000],
        analyzed_at=now,
    )

    # Replace any older analysis for the symbol with this fresh one.
    await session.execute(delete(NewsAnalysis).where(NewsAnalysis.symbol == symbol.upper()))
    session.add(analysis)
    await session.commit()
    return analysis