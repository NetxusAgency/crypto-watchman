import httpx


class NewsHttpClient:
    """Single shared httpx client for all news sources (mirrors price_fetcher pattern)."""

    def __init__(self) -> None:
        self.client = httpx.AsyncClient(
            timeout=15.0,
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
                )
            },
        )

    async def close(self) -> None:
        await self.client.aclose()


news_http = NewsHttpClient()