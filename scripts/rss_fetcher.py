from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

import feedparser
import requests
import yaml
from bs4 import BeautifulSoup
from dateutil import parser as date_parser


@dataclass(frozen=True)
class Source:
    id: str
    name: str
    url: str
    category: str = "general"


@dataclass(frozen=True)
class NewsItem:
    source_id: str
    source_name: str
    url: str
    title: str
    published_at: str | None  # ISO-8601 UTC
    content_text: str
    category: str = "general"


@dataclass(frozen=True)
class FetchReport:
    source_id: str
    source_url: str
    ok: bool
    item_count: int
    status_code: int | None = None
    error: str | None = None


USER_AGENT = (
    "Mozilla/5.0 (compatible; rss-ai-email-digest/1.0; "
    "+https://github.com/lhjlife-sys/ai_daily_news)"
)


def _request_headers() -> dict[str, str]:
    return {
        "User-Agent": USER_AGENT,
        "Accept": "application/rss+xml, application/xml, text/xml;q=0.9, */*;q=0.8",
    }


def load_sources(config_path: str) -> list[Source]:
    data = yaml.safe_load(open(config_path, "r", encoding="utf-8"))
    sources = data.get("sources", []) if isinstance(data, dict) else []
    out: list[Source] = []
    for s in sources:
        if not isinstance(s, dict):
            continue
        sid = str(s.get("id", "")).strip()
        name = str(s.get("name", sid)).strip() or sid
        url = str(s.get("url", "")).strip()
        category = str(s.get("category", "general")).strip().lower() or "general"
        if sid and url:
            out.append(Source(id=sid, name=name, url=url, category=category))
    return out


def _html_to_text(html: str) -> str:
    soup = BeautifulSoup(html or "", "html.parser")
    text = soup.get_text(" ", strip=True)
    return " ".join(text.split())


def _parse_datetime_to_utc_iso(value: str | None) -> str | None:
    if not value:
        return None
    try:
        dt = date_parser.parse(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt = dt.astimezone(timezone.utc).replace(microsecond=0)
        return dt.isoformat()
    except Exception:
        return None


def _entry_to_news_item(source: Source, e) -> NewsItem | None:
    url = (getattr(e, "link", None) or getattr(e, "id", None) or "").strip()
    title = (getattr(e, "title", None) or "").strip()
    published_raw = getattr(e, "published", None) or getattr(e, "updated", None) or None
    published_at = _parse_datetime_to_utc_iso(published_raw)

    content_html = ""
    if hasattr(e, "content") and e.content:
        try:
            content_html = e.content[0].value or ""
        except Exception:
            content_html = ""
    if not content_html:
        content_html = getattr(e, "summary", None) or getattr(e, "description", None) or ""

    content_text = _html_to_text(content_html)
    if not url or not title:
        return None
    return NewsItem(
        source_id=source.id,
        source_name=source.name,
        url=url,
        title=title,
        published_at=published_at,
        content_text=content_text,
        category=source.category,
    )


def fetch_source(source: Source, timeout: int = 20) -> list[NewsItem]:
    response = requests.get(
        source.url,
        headers=_request_headers(),
        timeout=timeout,
    )
    response.raise_for_status()
    parsed = feedparser.parse(response.content)
    items: list[NewsItem] = []
    for e in parsed.entries or []:
        item = _entry_to_news_item(source, e)
        if item:
            items.append(item)
    return items


def fetch_all(sources: Iterable[Source], timeout: int = 20) -> tuple[list[NewsItem], list[FetchReport]]:
    all_items: list[NewsItem] = []
    reports: list[FetchReport] = []
    for s in sources:
        try:
            response = requests.get(
                s.url,
                headers=_request_headers(),
                timeout=timeout,
            )
            status_code = response.status_code
            response.raise_for_status()
            parsed = feedparser.parse(response.content)
            items: list[NewsItem] = []
            for e in parsed.entries or []:
                item = _entry_to_news_item(s, e)
                if item:
                    items.append(item)
            all_items.extend(items)
            reports.append(
                FetchReport(
                    source_id=s.id,
                    source_url=s.url,
                    ok=True,
                    item_count=len(items),
                    status_code=status_code,
                    error=None,
                )
            )
        except Exception as e:
            print(f"[warn] failed to fetch source {s.id} ({s.url}): {e}", flush=True)
            reports.append(
                FetchReport(
                    source_id=s.id,
                    source_url=s.url,
                    ok=False,
                    item_count=0,
                    status_code=None,
                    error=str(e),
                )
            )
    return all_items, reports


def sort_items_newest_first(items: Iterable[NewsItem]) -> list[NewsItem]:
    def key(i: NewsItem) -> tuple[int, str]:
        if i.published_at is None:
            return (0, "")
        try:
            dt = datetime.fromisoformat(i.published_at)
            return (1, dt.isoformat())
        except Exception:
            return (0, "")

    return sorted(list(items), key=key, reverse=True)
