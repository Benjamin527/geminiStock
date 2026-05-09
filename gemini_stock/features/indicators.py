from __future__ import annotations

import numpy as np
import pandas as pd


def _series(df: pd.DataFrame, name: str) -> pd.Series:
    return pd.to_numeric(df[name], errors="coerce").astype(float)


def ema(close: pd.Series, span: int) -> pd.Series:
    return close.ewm(span=span, adjust=False, min_periods=1).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    value = 100 - (100 / (1 + rs))
    return value.fillna(100)


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[pd.Series, pd.Series, pd.Series]:
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def vwap(df: pd.DataFrame) -> pd.Series:
    high = _series(df, "high")
    low = _series(df, "low")
    close = _series(df, "close")
    volume = _series(df, "volume")
    typical_price = (high + low + close) / 3
    cumulative_volume = volume.cumsum().replace(0, np.nan)
    return (typical_price * volume).cumsum() / cumulative_volume


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = _series(df, "high")
    low = _series(df, "low")
    close = _series(df, "close")
    previous_close = close.shift(1)
    true_range = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


def enrich_indicators(df: pd.DataFrame) -> pd.DataFrame:
    enriched = df.copy()
    close = _series(enriched, "close")
    enriched["rsi_14"] = rsi(close, 14)
    macd_line, signal_line, histogram = macd(close)
    enriched["macd"] = macd_line
    enriched["macd_signal"] = signal_line
    enriched["macd_histogram"] = histogram
    enriched["ema_20"] = ema(close, 20)
    enriched["ema_50"] = ema(close, 50)
    enriched["vwap"] = vwap(enriched)
    enriched["atr_14"] = atr(enriched, 14)
    return enriched

