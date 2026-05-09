from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd
import requests

from gemini_stock.data.base import (
    DelayedMarketDataError,
    EmptyMarketDataError,
    MarketDataError,
    MarketDataProvider,
    normalize_ohlcv_frame,
)
from gemini_stock.schedule import MarketSession, classify_market_session


class PolygonMarketDataProvider(MarketDataProvider):
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.polygon.io",
        delayed_tolerance_minutes: int = 30,
        timeout_seconds: int = 20,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.delayed_tolerance = timedelta(minutes=delayed_tolerance_minutes)
        self.timeout_seconds = timeout_seconds

    def get_ohlcv(self, symbol: str, interval: str, period: str) -> pd.DataFrame:
        multiplier, timespan = _interval_to_polygon(interval)
        end = datetime.now(timezone.utc)
        start = end - _period_to_timedelta(period)
        url = f"{self.base_url}/v2/aggs/ticker/{symbol}/range/{multiplier}/{timespan}/{start.date().isoformat()}/{end.date().isoformat()}"
        try:
            response = requests.get(
                url,
                params={
                    "adjusted": "false",
                    "sort": "asc",
                    "limit": 50000,
                    "apiKey": self.api_key,
                },
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            raise MarketDataError(f"failed to fetch {symbol} {interval} from Polygon: {exc}") from exc
        rows = payload.get("results") or []
        if not rows:
            raise EmptyMarketDataError(f"empty Polygon response for {symbol} {interval}")
        df = pd.DataFrame([_row_to_ohlcv(row) for row in rows])
        normalized = normalize_ohlcv_frame(df)
        self._ensure_not_delayed(symbol, interval, normalized)
        return normalized

    def _ensure_not_delayed(self, symbol: str, interval: str, df: pd.DataFrame) -> None:
        current_time = datetime.now(timezone.utc)
        if classify_market_session(current_time) == MarketSession.CLOSED:
            return
        latest = pd.Timestamp(df.iloc[-1]["timestamp"]).to_pydatetime().astimezone(timezone.utc)
        age = current_time - latest
        if age > self.delayed_tolerance:
            raise DelayedMarketDataError(
                f"{symbol} {interval} latest Polygon candle is delayed by {age.total_seconds() / 60:.1f} minutes"
            )


def _row_to_ohlcv(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "timestamp": datetime.fromtimestamp(int(row["t"]) / 1000, tz=timezone.utc),
        "open": row["o"],
        "high": row["h"],
        "low": row["l"],
        "close": row["c"],
        "volume": row.get("v", 0),
    }


def _interval_to_polygon(interval: str) -> tuple[int, str]:
    if interval == "1m":
        return 1, "minute"
    if interval == "15m":
        return 15, "minute"
    raise MarketDataError(f"unsupported Polygon interval: {interval}")


def _period_to_timedelta(period: str) -> timedelta:
    unit = period[-1:]
    try:
        amount = int(period[:-1])
    except ValueError as exc:
        raise MarketDataError(f"unsupported period: {period}") from exc
    if unit == "d":
        return timedelta(days=amount)
    if unit == "w":
        return timedelta(weeks=amount)
    raise MarketDataError(f"unsupported period: {period}")
