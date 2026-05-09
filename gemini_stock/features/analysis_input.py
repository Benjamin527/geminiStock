from __future__ import annotations

from datetime import timezone

import pandas as pd

from gemini_stock.features.events import macd_histogram_last_3
from gemini_stock.schemas import GeminiSignal, NewsItem, OHLCVSummary, TechnicalAnalysisInput, TechnicalSnapshot


def _round(value: float | int | None, digits: int = 4) -> float | None:
    if value is None or pd.isna(value):
        return None
    return round(float(value), digits)


def _recent_ohlcv(candles_15m: pd.DataFrame, limit: int = 20) -> list[OHLCVSummary]:
    df = candles_15m.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    rows = []
    for _, row in df.sort_values("timestamp").tail(limit).iterrows():
        rows.append(
            OHLCVSummary(
                timestamp_utc=pd.Timestamp(row["timestamp"]).to_pydatetime().astimezone(timezone.utc),
                open=round(float(row["open"]), 4),
                high=round(float(row["high"]), 4),
                low=round(float(row["low"]), 4),
                close=round(float(row["close"]), 4),
                volume=int(row["volume"]),
            )
        )
    return rows


def _volume_regime(candles_15m: pd.DataFrame) -> str:
    volume = pd.to_numeric(candles_15m["volume"], errors="coerce")
    last = float(volume.iloc[-1])
    average = float(volume.tail(20).mean())
    if average <= 0:
        return "normal"
    ratio = last / average
    if ratio >= 1.5:
        return "spike"
    if ratio >= 1.15:
        return "high"
    if ratio <= 0.7:
        return "low"
    return "normal"


def _trend_regime(snapshot: TechnicalSnapshot) -> str:
    if snapshot.ema_20 is None or snapshot.ema_50 is None:
        return "mixed"
    if snapshot.close >= snapshot.ema_20 >= snapshot.ema_50:
        return "bullish"
    if snapshot.close <= snapshot.ema_20 <= snapshot.ema_50:
        return "bearish"
    return "mixed"


def build_news_summary(news_items: list[NewsItem]) -> list[str]:
    return [
        f"{item.source}: {item.title} ({item.published_at.isoformat()}) - {item.summary[:240]}"
        for item in news_items[:8]
    ]


def build_analysis_input(
    symbol: str,
    candles_15m: pd.DataFrame,
    snapshot: TechnicalSnapshot,
    technical_events: list[str],
    news_summary: list[str],
) -> TechnicalAnalysisInput:
    return TechnicalAnalysisInput(
        symbol=symbol,
        timestamp_utc=snapshot.timestamp_utc,
        last_price=snapshot.close,
        ohlcv_recent=_recent_ohlcv(candles_15m),
        rsi_14=snapshot.rsi_14,
        macd=snapshot.macd,
        macd_signal=snapshot.macd_signal,
        macd_histogram=snapshot.macd_histogram,
        macd_histogram_last_3=macd_histogram_last_3(candles_15m),
        ema_20=snapshot.ema_20,
        ema_50=snapshot.ema_50,
        vwap=snapshot.vwap,
        atr_14=snapshot.atr_14,
        support_levels=snapshot.support_levels,
        resistance_levels=snapshot.resistance_levels,
        volume_regime=_volume_regime(candles_15m),
        trend_regime=_trend_regime(snapshot),  # type: ignore[arg-type]
        technical_events=technical_events,
        news_summary=news_summary,
    )


def _histogram_two_bar_trend(values: list[float]) -> bool:
    return len(values) >= 3 and ((values[-1] > values[-2] > values[-3]) or (values[-1] < values[-2] < values[-3]))


def is_strong_candidate(
    snapshot: TechnicalSnapshot,
    analysis_input: TechnicalAnalysisInput,
    technical_events: list[str],
    news_items: list[NewsItem],
    preliminary_signal: GeminiSignal | None = None,
) -> bool:
    rsi = snapshot.rsi_14
    if rsi is not None and (rsi < 35 or rsi > 65):
        return True
    if _histogram_two_bar_trend(analysis_input.macd_histogram_last_3):
        return True
    if "price_near_support" in technical_events or "price_near_resistance" in technical_events:
        return True
    if any(item.relevance_score >= 0.85 for item in news_items):
        return True
    if preliminary_signal is not None and abs(preliminary_signal.sentiment_score) > 6:
        return True
    return False
