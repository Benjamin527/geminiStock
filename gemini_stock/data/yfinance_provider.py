from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone

import pandas as pd
import yfinance as yf

from gemini_stock.data.base import (
    DelayedMarketDataError,
    EmptyMarketDataError,
    MarketDataError,
    MarketDataProvider,
    normalize_ohlcv_frame,
)
from gemini_stock.schedule import MarketSession, classify_market_session
from gemini_stock.schemas import Candle


class YFinanceMarketDataProvider(MarketDataProvider):
    def __init__(
        self,
        delayed_tolerance_minutes: int = 30,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self.delayed_tolerance = timedelta(minutes=delayed_tolerance_minutes)
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))

    def get_ohlcv(self, symbol: str, interval: str, period: str) -> pd.DataFrame:
        try:
            raw = yf.download(
                tickers=symbol,
                period=period,
                interval=interval,
                auto_adjust=False,
                prepost=True,
                progress=False,
                threads=False,
            )
        except Exception as exc:  # pragma: no cover - depends on network/provider behavior
            raise MarketDataError(f"failed to fetch {symbol} {interval}: {exc}") from exc
        if raw is None or raw.empty:
            raise EmptyMarketDataError(f"empty yfinance response for {symbol} {interval}")

        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)
        df = normalize_ohlcv_frame(raw)
        self._ensure_not_delayed(symbol, interval, df)
        return df

    def _ensure_not_delayed(self, symbol: str, interval: str, df: pd.DataFrame) -> None:
        current_time = self._now_fn().astimezone(timezone.utc)
        session = classify_market_session(current_time)
        if session == MarketSession.CLOSED:
            return
        latest = pd.Timestamp(df.iloc[-1]["timestamp"]).to_pydatetime().astimezone(timezone.utc)
        age = current_time - latest
        if age > self.delayed_tolerance:
            raise DelayedMarketDataError(
                f"{symbol} {interval} latest candle is delayed by {age.total_seconds() / 60:.1f} minutes"
            )


def candles_from_frame(symbol: str, interval: str, df: pd.DataFrame) -> list[Candle]:
    return [
        Candle(
            symbol=symbol,
            interval=interval,  # type: ignore[arg-type]
            timestamp_utc=pd.Timestamp(row["timestamp"]).to_pydatetime(),
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=int(row["volume"]),
        )
        for _, row in df.iterrows()
    ]
