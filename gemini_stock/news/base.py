from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from html import unescape
import re
from typing import Iterable

import yfinance as yf

from gemini_stock.schemas import NewsItem


class NewsProvider(ABC):
    @abstractmethod
    def get_news(self, symbols: list[str]) -> list[NewsItem]:
        """Return cleaned, normalized news items."""


class NullNewsProvider(NewsProvider):
    def get_news(self, symbols: list[str]) -> list[NewsItem]:
        return []


class YFinanceNewsProvider(NewsProvider):
    def get_news(self, symbols: list[str]) -> list[NewsItem]:
        raw_items = []
        for symbol in symbols:
            try:
                raw_items.extend(_extract_yfinance_news_items(yf.Ticker(symbol).news, symbol))
            except Exception:
                continue
        return normalize_news_items(raw_items, symbols)


def _extract_yfinance_news_items(items: Iterable[dict], symbol: str) -> list[dict]:
    extracted = []
    for item in items or []:
        content = item.get("content") if isinstance(item.get("content"), dict) else item
        title = str(content.get("title") or "").strip()
        if not title:
            continue
        url = _news_url(content)
        if not url:
            continue
        extracted.append(
            {
                "title": title,
                "source": _news_source(content),
                "published_at": _news_timestamp(content),
                "summary": content.get("summary") or content.get("description") or "",
                "url": url,
                "symbols": [symbol],
            }
        )
    return extracted


def _news_url(content: dict) -> str:
    canonical = content.get("canonicalUrl")
    if isinstance(canonical, dict) and canonical.get("url"):
        return str(canonical["url"])
    click_through = content.get("clickThroughUrl")
    if isinstance(click_through, dict) and click_through.get("url"):
        return str(click_through["url"])
    return str(content.get("link") or content.get("url") or "").strip()


def _news_source(content: dict) -> str:
    provider = content.get("provider")
    if isinstance(provider, dict):
        return str(provider.get("displayName") or provider.get("name") or "Yahoo Finance")
    return str(content.get("publisher") or "Yahoo Finance")


def _news_timestamp(content: dict) -> datetime:
    raw = content.get("pubDate") or content.get("displayTime") or content.get("providerPublishTime")
    if isinstance(raw, (int, float)):
        return datetime.fromtimestamp(raw, tz=timezone.utc)
    if isinstance(raw, str) and raw:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
    return datetime.now(timezone.utc)


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
        explicit_symbols = [str(symbol).upper() for symbol in item.get("symbols", [])]
        related = [
            symbol
            for symbol in symbols
            if symbol.upper() in explicit_symbols or symbol.upper() in f"{title} {summary}".upper()
        ]
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
