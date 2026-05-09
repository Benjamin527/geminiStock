from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, time
from zoneinfo import ZoneInfo

import pandas as pd


NEW_YORK = ZoneInfo("America/New_York")


class MarketDataError(RuntimeError):
    pass


class EmptyMarketDataError(MarketDataError):
    pass


class DelayedMarketDataError(MarketDataError):
    pass


class MarketDataProvider(ABC):
    @abstractmethod
    def get_ohlcv(self, symbol: str, interval: str, period: str) -> pd.DataFrame:
        """Return OHLCV rows with timestamp, open, high, low, close, volume."""


def is_us_market_session(now: datetime | None = None) -> bool:
    current = (now or datetime.now(NEW_YORK)).astimezone(NEW_YORK)
    if current.weekday() >= 5:
        return False
    return time(9, 30) <= current.time() <= time(16, 0)


def normalize_ohlcv_frame(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        raise EmptyMarketDataError("market data provider returned empty data")
    normalized = df.copy()
    if "Datetime" in normalized.columns:
        normalized = normalized.rename(columns={"Datetime": "timestamp"})
    elif "Date" in normalized.columns:
        normalized = normalized.rename(columns={"Date": "timestamp"})
    elif normalized.index.name:
        normalized = normalized.reset_index().rename(columns={normalized.index.name: "timestamp"})
    else:
        normalized = normalized.reset_index().rename(columns={"index": "timestamp"})

    rename_map = {
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "close",
        "Volume": "volume",
    }
    normalized = normalized.rename(columns=rename_map)
    required = ["timestamp", "open", "high", "low", "close", "volume"]
    missing = [column for column in required if column not in normalized.columns]
    if missing:
        raise MarketDataError(f"market data missing columns: {missing}")
    normalized = normalized[required].copy()
    normalized["timestamp"] = pd.to_datetime(normalized["timestamp"], utc=True)
    normalized = normalized.dropna(subset=["open", "high", "low", "close"])
    normalized["volume"] = normalized["volume"].fillna(0).astype(int)
    if normalized.empty:
        raise EmptyMarketDataError("market data became empty after normalization")
    return normalized.sort_values("timestamp").reset_index(drop=True)

