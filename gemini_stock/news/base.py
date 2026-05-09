from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from html import unescape
import re
from typing import Iterable

from gemini_stock.schemas import NewsItem


class NewsProvider(ABC):
    @abstractmethod
    def get_news(self, symbols: list[str]) -> list[NewsItem]:
        """Return cleaned, normalized news items."""


class NullNewsProvider(NewsProvider):
    def get_news(self, symbols: list[str]) -> list[NewsItem]:
        return []


def clean_summary(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value or "")
    text = re.sub(r"\s+", " ", unescape(text)).strip()
    return text[:1200]


def relevance_score(title: str, summary: str, symbols: Iterable[str]) -> float:
    symbol_list = list(symbols)
    corpus = f"{title} {summary}".upper()
    matches = sum(1 for symbol in symbol_list if symbol.upper() in corpus)
    return min(1.0, matches / max(1, len(symbol_list)))


def normalize_news_items(raw_items: Iterable[dict], symbols: list[str]) -> list[NewsItem]:
    seen: set[str] = set()
    normalized: list[NewsItem] = []
    for item in raw_items:
        url = str(item.get("url", "")).strip()
        title = str(item.get("title", "")).strip()
        if not url or not title or url in seen:
            continue
        seen.add(url)
        published_at = item.get("published_at") or datetime.now(timezone.utc)
        if isinstance(published_at, str):
            published_at = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        summary = clean_summary(str(item.get("summary", "")))
        related = [symbol for symbol in symbols if symbol.upper() in f"{title} {summary}".upper()]
        normalized.append(
            NewsItem(
                title=title,
                source=str(item.get("source", "unknown")),
                published_at=published_at,
                summary=summary,
                url=url,
                related_symbols=related,
                relevance_score=relevance_score(title, summary, symbols),
            )
        )
    return sorted(normalized, key=lambda news: news.published_at, reverse=True)
