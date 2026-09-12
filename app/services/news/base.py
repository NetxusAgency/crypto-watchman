import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}


@dataclass
class NormalizedArticle:
    title: str
    source: str
    url: str
    description: str | None = None
    content: str | None = None
    published_at: datetime | None = None
    author: str | None = None
    image_url: str | None = None
    language: str | None = None
    assets: list[str] = field(default_factory=list)
    fetched_at: datetime | None = None


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).replace(tzinfo=None)
    except Exception:
        pass
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def strip_html(text: str | None) -> str:
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(text.split())


def parse_feed(
    xml_bytes: bytes,
    source: str,
    max_items: int,
    keyword: str | None = None,
) -> list[NormalizedArticle]:
    """Parse RSS 2.0 or Atom XML into NormalizedArticle objects."""
    try:
        root = ElementTree.fromstring(xml_bytes)
    except ElementTree.ParseError:
        return []

    articles: list[NormalizedArticle] = []
    if root.tag.lower().endswith("rss"):
        for item in root.findall(".//item") or []:
            title = strip_html(item.findtext("title") or "")
            link = (item.findtext("link") or "").strip()
            desc = strip_html(item.findtext("description"))
            articles.append(
                NormalizedArticle(
                    title=title,
                    source=source,
                    url=link,
                    description=desc or None,
                    published_at=parse_datetime(item.findtext("pubDate")),
                    author=item.findtext("author"),
                )
            )
    else:
        entries = root.findall("atom:entry", ATOM_NS) or root.findall("entry")
        for entry in entries:
            title = strip_html(entry.findtext("atom:title", "", ATOM_NS) or entry.findtext("title", ""))
            link_el = entry.find("atom:link", ATOM_NS)
            if link_el is None:
                link_el = entry.find("link")
            link = (link_el.attrib.get("href", "") if link_el is not None else "").strip()
            summary = strip_html(
                entry.findtext("atom:summary", None, ATOM_NS) or entry.findtext("atom:content", None, ATOM_NS)
                or entry.findtext("summary") or entry.findtext("content")
            )
            published = parse_datetime(
                entry.findtext("atom:published", None, ATOM_NS)
                or entry.findtext("atom:updated", None, ATOM_NS)
                or entry.findtext("published") or entry.findtext("updated")
            )
            author_el = entry.find("atom:author", ATOM_NS)
            if author_el is None:
                author_el = entry.find("author")
            author = None
            if author_el is not None:
                author = author_el.findtext("atom:name", None, ATOM_NS) or author_el.findtext("name")
            articles.append(
                NormalizedArticle(
                    title=title,
                    source=source,
                    url=link,
                    description=summary or None,
                    published_at=published,
                    author=author,
                )
            )

    result = [a for a in articles if a.title]
    if keyword:
        kw = keyword.lower()
        result = [
            a for a in result
            if kw in a.title.lower() or (a.description or "").lower().count(kw) > 0
        ]
    return result[:max_items]


class NewsSource(ABC):
    name: str = "base"

    @abstractmethod
    async def fetch(self, query: str | None, max_items: int = 10) -> list[NormalizedArticle]:
        raise NotImplementedError