from __future__ import annotations

import pandas as pd

from gemini_stock.features.indicators import enrich_indicators
from gemini_stock.schemas import TechnicalSnapshot


def _last3_histograms(candles_15m: pd.DataFrame) -> list[float]:
    enriched = enrich_indicators(candles_15m.copy())
    values = enriched["macd_histogram"].dropna().tail(3).tolist()
    return [round(float(value), 4) for value in values]


def build_technical_events(candles_15m: pd.DataFrame, snapshot: TechnicalSnapshot) -> list[str]:
    events: list[str] = []
    price = snapshot.close
    atr = snapshot.atr_14 or 0
    near_threshold = 0.5 * atr if atr > 0 else max(price * 0.002, 0.01)

    if snapshot.support_levels and min(abs(price - level) for level in snapshot.support_levels) <= near_threshold:
        events.append("price_near_support")
    if snapshot.resistance_levels and min(abs(price - level) for level in snapshot.resistance_levels) <= near_threshold:
        events.append("price_near_resistance")

    rsi = snapshot.rsi_14
    if rsi is not None:
        if rsi < 35:
            events.append("rsi_oversold")
        if rsi > 65:
            events.append("rsi_overbought")

    enriched = enrich_indicators(candles_15m.copy())
    rsi_values = enriched["rsi_14"].dropna().tail(3).tolist()
    if len(rsi_values) >= 2 and min(rsi_values[:-1]) < 35 and rsi_values[-1] > rsi_values[-2]:
        events.append("rsi_recovering_from_oversold")

    hist = enriched["macd_histogram"].dropna().tail(3).tolist()
    if len(hist) >= 2 and hist[-1] > hist[-2] and hist[-1] > 0:
        events.append("macd_histogram_turning_positive")
    if len(hist) >= 2 and hist[-1] < hist[-2] and hist[-1] < 0:
        events.append("macd_histogram_turning_negative")

    if snapshot.vwap is not None:
        events.append("price_above_vwap" if price >= snapshot.vwap else "price_below_vwap")
    if snapshot.ema_20 is not None:
        events.append("price_above_ema20" if price >= snapshot.ema_20 else "price_below_ema20")
    if snapshot.ema_50 is not None:
        events.append("price_above_ema50" if price >= snapshot.ema_50 else "price_below_ema50")

    volume = float(candles_15m.iloc[-1]["volume"])
    average_volume = float(pd.to_numeric(candles_15m["volume"], errors="coerce").tail(20).mean())
    if average_volume > 0 and volume >= 1.5 * average_volume:
        events.append("volume_spike")

    if "price_near_resistance" in events and "volume_spike" in events:
        events.append("breakout_candidate")
    if "price_near_support" in events and "volume_spike" in events and "price_below_ema50" in events:
        events.append("breakdown_candidate")

    return events


def macd_histogram_last_3(candles_15m: pd.DataFrame) -> list[float]:
    return _last3_histograms(candles_15m)
