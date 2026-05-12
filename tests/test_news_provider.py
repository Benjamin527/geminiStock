from datetime import timezone

from gemini_stock.config import Settings
from gemini_stock.main import RuntimeContext
from gemini_stock.news.base import YFinanceNewsProvider


def test_yfinance_news_provider_normalizes_ticker_news(monkeypatch):
    class FakeTicker:
        @property
        def news(self):
            return [
                {
                    "content": {
                        "title": "Tesla rallies after delivery update",
                        "summary": "TSLL traders watch Tesla momentum.",
                        "provider": {"displayName": "Yahoo Finance"},
                        "pubDate": "2026-01-05T14:30:00Z",
                        "canonicalUrl": {"url": "https://example.com/tesla"},
                    }
                }
            ]

    monkeypatch.setattr("gemini_stock.news.base.yf.Ticker", lambda symbol: FakeTicker())

    items = YFinanceNewsProvider().get_news(["TSLL"])

    assert len(items) == 1
    assert items[0].title == "Tesla rallies after delivery update"
    assert items[0].source == "Yahoo Finance"
    assert items[0].published_at.tzinfo is not None
    assert items[0].published_at.astimezone(timezone.utc).isoformat().startswith("2026-01-05T14:30:00")
    assert items[0].related_symbols == ["TSLL"]


def test_runtime_context_uses_yfinance_news_provider_when_enabled():
    context = RuntimeContext.from_settings(Settings(news_provider="yfinance"))

    assert isinstance(context.news_provider, YFinanceNewsProvider)
