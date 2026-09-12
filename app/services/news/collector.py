import hashlib
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.database.models import NewsArticle
from app.services.news.base import NormalizedArticle
from app.services.news.queries import asset_name, build_queries, is_news_symbol
from app.services.news.sources import google_news, rss_source, exchange_source


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def article_hash(url: str, title: str) -> str:
    key = (url or "").strip().lower() or re.sub(r"[^a-z0-9 ]", "", (title or "").lower())
    return hashlib.sha256(key.encode()).hexdigest()


async def collect_for_symbol(session: AsyncSession, symbol: str, limit: int = 40) -> list[NormalizedArticle]:
    """Fetch + filter articles relevant to one asset (no DB writes)."""
    if not is_news_symbol(symbol):
        return []

    seen: set[str] = set()
    items: list[NormalizedArticle] = []
    queries = build_queries(symbol, settings.NEWS_MAX_QUERIES_PER_ASSET)

    def push(item: NormalizedArticle) -> None:
        h = article_hash(item.url, item.title)
        if h in seen:
            return
        seen.add(h)
        item.assets = [symbol]
        items.append(item)

    for query in queries:
        try:
            for a in await google_news.fetch(query, max_items=10):
                push(a)
        except Exception:
            continue
        try:
            for a in await rss_source.fetch(query, max_items=3):
                push(a)
        except Exception:
            continue

    # Exchange announcements: global feed filtered by the asset name.
    name = asset_name(symbol).lower()
    try:
        for a in await exchange_source.fetch(name, max_items=5):
            push(a)
    except Exception:
        pass

    return items[:limit]


async def store_articles(session: AsyncSession, symbol: str, items: list[NormalizedArticle]) -> int:
    """Persist articles that are not already stored. Returns number of new rows."""
    added = 0
    expires_at = _utcnow() + timedelta(hours=settings.NEWS_RETENTION_HOURS)
    now = _utcnow()

    for item in items:
        h = article_hash(item.url, item.title)
        exists = await session.execute(select(NewsArticle.id).where(NewsArticle.hash == h))
        if exists.scalar_one_or_none():
            continue
        session.add(
            NewsArticle(
                source=item.source[:50],
                source_url=item.url,
                title=(item.title or "Untitled")[:2000],
                description=(item.description or "")[:4000] or None,
                content=(item.content or "")[:8000] or None,
                published_at=item.published_at,
                fetched_at=now,
                author=(item.author or "")[:200] or None,
                image_url=item.image_url,
                language=item.language,
                hash=h,
                related_assets=symbol,
                processing_status="pending",
                expires_at=expires_at,
            )
        )
        added += 1

    if added:
        await session.commit()
    return added