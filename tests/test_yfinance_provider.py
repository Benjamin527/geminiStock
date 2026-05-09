from datetime import datetime, timezone

import pandas as pd
import pytest

from gemini_stock.data.base import DelayedMarketDataError
from gemini_stock.data.yfinance_provider import YFinanceMarketDataProvider


def _raw_frame(timestamp: datetime) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Datetime": pd.Timestamp(timestamp),
                "Open": 100.0,
                "High": 101.0,
                "Low": 99.0,
                "Close": 100.5,
                "Volume": 1000,
            }
        ]
    )


def test_yfinance_download_includes_prepost_data(monkeypatch):
    captured = {}

    def fake_download(**kwargs):
        captured.update(kwargs)
        return _raw_frame(datetime(2026, 4, 28, 13, 0, tzinfo=timezone.utc))

    monkeypatch.setattr("gemini_stock.data.yfinance_provider.yf.download", fake_download)
    provider = YFinanceMarketDataProvider(
        now_fn=lambda: datetime(2026, 4, 28, 13, 5, tzinfo=timezone.utc),
    )

    provider.get_ohlcv("TQQQ", "15m", "5d")

    assert captured["prepost"] is True


def test_yfinance_rejects_stale_premarket_candles(monkeypatch):
    def fake_download(**kwargs):
        return _raw_frame(datetime(2026, 4, 27, 19, 45, tzinfo=timezone.utc))

    monkeypatch.setattr("gemini_stock.data.yfinance_provider.yf.download", fake_download)
    provider = YFinanceMarketDataProvider(
        delayed_tolerance_minutes=30,
        now_fn=lambda: datetime(2026, 4, 28, 13, 5, tzinfo=timezone.utc),
    )

    with pytest.raises(DelayedMarketDataError):
        provider.get_ohlcv("TQQQ", "15m", "5d")
