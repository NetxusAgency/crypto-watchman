from datetime import datetime

from app.database.models import NewsAnalysis
from app.services.news.analyzer import _coerce_sentiment, _extract_json
from app.services.news.base import parse_datetime, parse_feed, strip_html
from app.services.news.collector import article_hash
from app.services.news.news_service import format_analysis
from app.services.news.queries import build_queries, is_news_symbol
from app.services.news.sources import RSSNewsSource, NormalizedArticle

RSS_XML = b"""<?xml version="1.0" encoding="UTF-8" ?>
<rss version="2.0"><channel>
  <title>Test Feed</title>
  <item>
    <title>Bitcoin ETF inflows surge</title>
    <link>https://example.com/btc</link>
    <description><![CDATA[<b>Bitcoin</b> sees record inflows.]]></description>
    <pubDate>Mon, 02 Sep 2026 10:00:00 GMT</pubDate>
    <author>Test Author</author>
  </item>
  <item>
    <title>Ethereum upgrade shipped</title>
    <link>https://example.com/eth</link>
    <description>Ethereum Pectra upgrade is live.</description>
    <pubDate>Mon, 02 Sep 2026 11:00:00 GMT</pubDate>
  </item>
</channel></rss>"""

ATOM_XML = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom Feed</title>
  <entry>
    <title>Solana outage reported</title>
    <link href="https://example.com/sol"/>
    <summary>Solana experienced a brief network issue.</summary>
    <published>2026-09-02T12:00:00Z</published>
    <author><name>Ann</name></author>
  </entry>
</feed>"""


class TestFeedParsing:
    def test_parse_rss(self):
        articles = parse_feed(RSS_XML, "rss", 10)
        assert len(articles) == 2
        assert articles[0].title == "Bitcoin ETF inflows surge"
        assert articles[0].url == "https://example.com/btc"
        assert "<b>" not in (articles[0].description or "")
        assert isinstance(articles[0].published_at, datetime)
        assert articles[0].author == "Test Author"

    def test_parse_rss_keyword_filter(self):
        articles = parse_feed(RSS_XML, "rss", 10, keyword="ethereum")
        assert len(articles) == 1
        assert articles[0].title == "Ethereum upgrade shipped"

    def test_parse_atom(self):
        articles = parse_feed(ATOM_XML, "atom", 10)
        assert len(articles) == 1
        assert articles[0].title == "Solana outage reported"
        assert articles[0].url == "https://example.com/sol"
        assert articles[0].published_at == datetime(2026, 9, 2, 12, 0, 0)

    def test_parse_bad_xml_returns_empty(self):
        assert parse_feed(b"not-xml", "rss", 10) == []

    def test_strip_html(self):
        assert strip_html("<p>Hello <b>world</b></p>") == "Hello world"


class TestDates:
    def test_rfc822(self):
        assert parse_datetime("Mon, 02 Sep 2026 10:00:00 GMT") == datetime(2026, 9, 2, 10, 0, 0)

    def test_iso(self):
        assert parse_datetime("2026-09-02T12:00:00Z") == datetime(2026, 9, 2, 12, 0, 0)

    def test_none(self):
        assert parse_datetime(None) is None
        assert parse_datetime("not a date") is None


class TestHashing:
    def test_deterministic(self):
        assert article_hash("https://x.com/1", "Title") == article_hash("https://x.com/1", "Title")

    def test_distinct(self):
        assert article_hash("https://x.com/1", "Title A") != article_hash("https://x.com/2", "Title B")

    def test_whitespace_insensitive(self):
        assert article_hash("  https://x.com/1 ", "title") == article_hash("https://x.com/1", "title")


class TestSymbolsAndQueries:
    def test_forex_excluded(self):
        assert not is_news_symbol("EURUSD")
        assert not is_news_symbol("GBPUSD")

    def test_crypto_included(self):
        assert is_news_symbol("BTC")
        assert is_news_symbol("ETH")

    def test_build_queries_uses_full_name(self):
        queries = build_queries("BTC", 3)
        assert len(queries) == 3
        assert all("bitcoin" in q.lower() for q in queries)

    def test_build_queries_unknown_symbol(self):
        queries = build_queries("FOOBAR", 3)
        assert queries == ["FOOBAR crypto news"]


class TestAnalyzerHelpers:
    def test_extract_json_from_code_fence(self):
        text = '```json\n{"overall_sentiment": "BULLISH", "impact_score": 80}\n```'
        assert _extract_json(text)["overall_sentiment"] == "BULLISH"

    def test_extract_json_with_prose(self):
        text = 'Here you go:\n{"ok": true}'
        assert _extract_json(text)["ok"] is True

    def test_extract_json_none(self):
        assert _extract_json("No output") is None

    def test_coerce_sentiment(self):
        assert _coerce_sentiment("bullish") == "BULLISH"
        assert _coerce_sentiment("Neutral") == "NEUTRAL"
        assert _coerce_sentiment("garbage") is None


class TestSourceMatching:
    def test_matches_multiple_tokens(self):
        s = RSSNewsSource()
        article = NormalizedArticle(
            title="Bitcoin hits new high amid ETF flows",
            description="Strong trading volumes",
            source="rss",
            url="",
        )
        assert s._matches(article, "bitcoin") is True

    def test_matches_no_similar(self):
        s = RSSNewsSource()
        article = NormalizedArticle(
            title="Cardano roadmap update",
            description="",
            source="rss",
            url="",
        )
        assert s._matches(article, "ethereum") is False


class TestFormatting:
    def test_format_empty(self):
        text = format_analysis("BTC", None)
        assert "BTC" in text
        assert "No reliable news" in text

    def test_format_populated(self):
        a = NewsAnalysis(
            symbol="BTC",
            article_count=30,
            positive_count=18,
            negative_count=7,
            neutral_count=5,
            overall_sentiment="BULLISH",
            impact_level="MEDIUM",
            impact_score=67,
            potential_direction="UPWARD",
            confidence=72,
            event_categories="Exchange Listing, ETF",
            reason="ETF inflows dominate headlines.",
            time_horizon="Short term",
        )
        text = format_analysis("BTC", a)
        assert "BULLISH" in text
        assert "MEDIUM" in text
        assert "UPWARD" in text
        assert "72%" in text
        assert "18" in text
        assert "not a price prediction" in text