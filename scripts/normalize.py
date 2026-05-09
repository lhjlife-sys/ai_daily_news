from __future__ import annotations

from dataclasses import asdict

from .rss_fetcher import NewsItem
from .state_store import item_signature


def news_item_to_dict(item: NewsItem) -> dict:
    d = asdict(item)
    d["sig"] = item_signature(item.url, item.title, item.published_at)
    return d
