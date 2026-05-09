from __future__ import annotations

from datetime import timezone

import pandas as pd

from gemini_stock.features.indicators import enrich_indicators
from gemini_stock.schemas import TechnicalSnapshot


def _round(value: float | int | None, digits: int = 4) -> float | None:
    if value is None or pd.isna(value):
        return None
    return round(float(value), digits)


def _prepare(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        raise ValueError("cannot build technical snapshot from empty candles")
    required = {"timestamp", "open", "high", "low", "close", "volume"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"missing candle columns: {sorted(missing)}")
    prepared = df.copy()
    prepared["timestamp"] = pd.to_datetime(prepared["timestamp"], utc=True)
    prepared = prepared.sort_values("timestamp").dropna(subset=["open", "high", "low", "close"])
    if len(prepared) < 26:
        raise ValueError("at least 26 candles are required for MACD and snapshot calculation")
    return prepared


def _levels(enriched: pd.DataFrame, recent: pd.DataFrame) -> tuple[list[float], list[float]]:
    last_close = float(enriched.iloc[-1]["close"])
    supports = [float(recent["low"].min())]
    resistances = [float(recent["high"].max())]

    rolling_lows = enriched["low"].rolling(5, center=True).min()
    rolling_highs = enriched["high"].rolling(5, center=True).max()

    swing_lows = enriched.loc[enriched["low"].eq(rolling_lows), "low"].tail(5).tolist()
    swing_highs = enriched.loc[enriched["high"].eq(rolling_highs), "high"].tail(5).tolist()

    supports.extend(float(value) for value in swing_lows if value <= last_close)
    resistances.extend(float(value) for value in swing_highs if value >= last_close)

    support_levels = sorted({round(value, 2) for value in supports})[-3:]
    resistance_levels = sorted({round(value, 2) for value in resistances})[:3]
    return support_levels, resistance_levels


def build_technical_snapshot(symbol: str, candles_15m: pd.DataFrame) -> TechnicalSnapshot:
    prepared = _prepare(candles_15m)
    enriched = enrich_indicators(prepared)
    last = enriched.iloc[-1]
    recent = enriched.tail(16)
    support_levels, resistance_levels = _levels(enriched, recent)

    timestamp = pd.Timestamp(last["timestamp"]).to_pydatetime().astimezone(timezone.utc)
    return TechnicalSnapshot(
        symbol=symbol,
        timestamp_utc=timestamp,
        open=_round(last["open"]) or 0,
        high=_round(last["high"]) or 0,
        low=_round(last["low"]) or 0,
        close=_round(last["close"]) or 0,
        volume=int(last["volume"]),
        rsi_14=_round(last["rsi_14"]),
        macd=_round(last["macd"]),
        macd_signal=_round(last["macd_signal"]),
        macd_histogram=_round(last["macd_histogram"]),
        ema_20=_round(last["ema_20"]),
        ema_50=_round(last["ema_50"]),
        vwap=_round(last["vwap"]),
        atr_14=_round(last["atr_14"]),
        recent_4h_high=_round(recent["high"].max()) or 0,
        recent_4h_low=_round(recent["low"].min()) or 0,
        support_levels=support_levels,
        resistance_levels=resistance_levels,
    )

