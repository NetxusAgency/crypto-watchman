import httpx
import logging
from xml.etree import ElementTree

logger = logging.getLogger("crypto_watchman.sentiment_monitor")

NEWS_FEEDS = [
    "https://cointelegraph.com/rss",
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
]

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

class SentimentMonitor:
    def __init__(self):
        self.client = httpx.AsyncClient(timeout=10.0, headers={"User-Agent": "CryptoWatchmanBot/1.0"})

    async def close(self):
        await self.client.aclose()

    async def search_reddit(self, symbol: str) -> int:
        try:
            url = f"https://www.reddit.com/r/cryptocurrency/search.json?q={symbol}&restrict_sr=1&sort=new&t=day&limit=25"
            resp = await self.client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                posts = data.get("data", {}).get("children", [])
                count = 0
                for post in posts:
                    title = post.get("data", {}).get("title", "").upper()
                    selftext = (post.get("data", {}).get("selftext", "") or "").upper()
                    if symbol.upper() in title or symbol.upper() in selftext:
                        count += 1
                return count
            logger.warning(f"Reddit API returned {resp.status_code}")
        except Exception as e:
            logger.error(f"Error searching Reddit for {symbol}: {e}")
        return 0

    async def search_news(self, symbol: str) -> int:
        count = 0
        for feed_url in NEWS_FEEDS:
            try:
                resp = await self.client.get(feed_url, headers=BROWSER_HEADERS)
                if resp.status_code != 200:
                    continue
                tree = ElementTree.fromstring(resp.content)
                ns = {"ns": "http://www.w3.org/2005/Atom"}
                if tree.tag == "rss":
                    items = tree.findall(".//item")
                    for item in items:
                        title = (item.findtext("title") or "").upper()
                        desc = (item.findtext("description") or "").upper()
                        if symbol.upper() in title or symbol.upper() in desc:
                            count += 1
                else:
                    entries = tree.findall("ns:entry", ns) or tree.findall("entry")
                    for entry in entries:
                        title = (entry.findtext("title") or entry.findtext("ns:title", "", ns) or "").upper()
                        if symbol.upper() in title:
                            count += 1
            except Exception as e:
                logger.error(f"Error fetching news feed {feed_url}: {e}")
        return count

    async def get_mention_count(self, symbol: str) -> dict:
        reddit_count = await self.search_reddit(symbol)
        news_count = await self.search_news(symbol)
        return {
            "symbol": symbol.upper(),
            "reddit": reddit_count,
            "news": news_count,
            "total": reddit_count + news_count,
        }

sentiment_monitor = SentimentMonitor()
