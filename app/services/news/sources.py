import asyncio
import time
from datetime import datetime, timezone

from app.services.news.base import NewsSource, NormalizedArticle, parse_datetime, strip_html, parse_feed
from app.services.news.client import news_http

# Global crypto news feeds (RSS/Atom, no API keys required).
CRYPTO_RSS_FEEDS: list[str] = [
    "https://cointelegraph.com/rss",
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://decrypt.co/feed",
    "https://www.theblock.co/rss.xml",
    "https://cryptoslate.com/feed/",
    "https://bitcoinmagazine.com/feed",
    "https://www.newsbtc.com/feed/",
    "https://beincrypto.com/feed/",
]

# Official/public announcement endpoints (no keys). Schemas are external so every
# parser is defensive and fails soft. Bybit's v5 announcements endpoint now rejects
# our public requests, so it is skipped (kept commented for a future authenticated key).
BINANCE_CMS_URL = (
    "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query"
    "?type=1&pageNo=1&pageSize=10"
)
COINBASE_BLOG_RSS = "https://www.coinbase.com/blog.xml"

_TTL_SECONDS = 15 * 60


class GoogleNewsSource(NewsSource):
    name = "google_news"

    async def fetch(self, query: str, max_items: int = 10) -> list[NormalizedArticle]:
        if not query:
            return []
        try:
            resp = await news_http.client.get(
                "https://news.google.com/rss/search",
                params={"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"},
            )
            resp.raise_for_status()
        except Exception:
            return []
        return parse_feed(resp.content, self.name, max_items, None)


class RSSNewsSource(NewsSource):
    name = "rss"

    def __init__(self) -> None:
        self._cache: dict[str, tuple[float, list[NormalizedArticle]]] = {}

    async def fetch(self, query: str | None, max_items: int = 3) -> list[NormalizedArticle]:
        if not query:
            return []
        out: list[NormalizedArticle] = []
        for url in CRYPTO_RSS_FEEDS:
            cached = self._cache.get(url)
            now = time.time()
            if cached and (now - cached[0]) < _TTL_SECONDS:
                articles = cached[1]
            else:
                articles = await self._fetch_feed(url)
                self._cache[url] = (now, articles)
            for x in [x for x in articles if self._matches(x, query)]:
                out.append(x)
            if len(out) >= max_items:
                break
        return out[:max_items]

    async def _fetch_feed(self, url: str) -> list[NormalizedArticle]:
        try:
            resp = await news_http.client.get(url)
            resp.raise_for_status()
        except Exception:
            return []
        return parse_feed(resp.content, self.name, 50, None)

    @staticmethod
    def _matches(a: NormalizedArticle, query: str) -> bool:
        text = f"{a.title} {a.description or ''}".lower()
        tokens = [t for t in query.lower().replace('"', "").split() if len(t) > 3]
        if not tokens:
            tokens = query.lower().split()
        return all(t in text for t in tokens)


class ExchangeNewsSource(NewsSource):
    """Binance announcements + Coinbase blog; global feed filtered per query."""

    name = "exchange"

    def __init__(self) -> None:
        self._cache: dict[str, tuple[float, list[NormalizedArticle]]] = {}

    async def fetch(self, query: str | None, max_items: int = 10) -> list[NormalizedArticle]:
        items = await self._all(max_items)
        if not query:
            return items[:max_items]
        tokens = [t for t in query.lower().replace('"', "").split() if len(t) > 3]
        if not tokens:
            tokens = query.lower().split()
        matched = [
            a for a in items
            if all(t in f"{a.title} {a.description or ''}".lower() for t in tokens)
        ]
        return matched[:max_items]

    async def _all(self, max_items: int) -> list[NormalizedArticle]:
        now = time.time()
        out: list[NormalizedArticle] = []
        for key, fetcher in (
            ("binance", self._fetch_binance),
            ("coinbase", self._fetch_coinbase),
        ):
            cached = self._cache.get(key)
            if cached and (now - cached[0]) < _TTL_SECONDS:
                out.extend(cached[1])
            else:
                try:
                    articles = await asyncio.wait_for(fetcher(), timeout=8)
                except asyncio.TimeoutError:
                    articles = []
                except Exception:
                    articles = []
                self._cache[key] = (now, articles)
                out.extend(articles)
        return out[:max_items]

    async def _fetch_binance(self) -> list[NormalizedArticle]:
        try:
            resp = await news_http.client.get(BINANCE_CMS_URL)
            data = resp.json()
            articles = []
            for catalog in (data.get("data") or {}).get("catalogs") or []:
                for item in catalog.get("articles") or []:
                    title = strip_html(item.get("title") or "")
                    if not title:
                        continue
                    url = item.get("url") or ""
                    if url and not url.startswith("http"):
                        url = f"https://www.binance.com{url}"
                    articles.append(
                        NormalizedArticle(
                            title=title,
                            source=self.name,
                            url=url or "https://www.binance.com/en/support/announcement",
                            description=strip_html(item.get("description")),
                            published_at=parse_datetime(str(item.get("releaseDate")) if item.get("releaseDate") else None),
                        )
                    )
            return articles
        except Exception:
            return []

    async def _fetch_coinbase(self) -> list[NormalizedArticle]:
        try:
            resp = await news_http.client.get(COINBASE_BLOG_RSS)
            resp.raise_for_status()
            return parse_feed(resp.content, self.name, 20, None)
        except Exception:
            return []

    async def _fetch_binance(self) -> list[NormalizedArticle]:
        try:
            resp = await news_http.client.get(BINANCE_CMS_URL)
            data = resp.json()
            articles = []
            for catalog in (data.get("data") or {}).get("catalogs") or []:
                for item in catalog.get("articles") or []:
                    title = strip_html(item.get("title") or "")
                    if not title:
                        continue
                    url = item.get("url") or ""
                    if url and not url.startswith("http"):
                        url = f"https://www.binance.com{url}"
                    articles.append(
                        NormalizedArticle(
                            title=title,
                            source=self.name,
                            url=url or "https://www.binance.com/en/support/announcement",
                            description=strip_html(item.get("description")),
                            published_at=parse_datetime(str(item.get("releaseDate")) if item.get("releaseDate") else None),
                        )
                    )
            return articles
        except Exception:
            return []

    async def _fetch_coinbase(self) -> list[NormalizedArticle]:
        try:
            resp = await news_http.client.get(COINBASE_BLOG_RSS)
            resp.raise_for_status()
            return parse_feed(resp.content, self.name, 20, None)
        except Exception:
            return []


google_news = GoogleNewsSource()
rss_source = RSSNewsSource()
exchange_source = ExchangeNewsSource()