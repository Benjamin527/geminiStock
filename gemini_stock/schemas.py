from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator


class SerializableModel(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    def json_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class Candle(SerializableModel):
    symbol: str
    interval: Literal["1m", "15m"]
    timestamp_utc: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int

    @field_validator("timestamp_utc")
    @classmethod
    def timestamp_must_be_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("timestamp_utc must be timezone-aware")
        return value.astimezone(timezone.utc)


class TechnicalSnapshot(SerializableModel):
    symbol: str
    interval: Literal["15m"] = "15m"
    timestamp_utc: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    rsi_14: float | None
    macd: float | None
    macd_signal: float | None
    macd_histogram: float | None
    ema_20: float | None
    ema_50: float | None
    vwap: float | None
    atr_14: float | None
    recent_4h_high: float
    recent_4h_low: float
    support_levels: list[float]
    resistance_levels: list[float]


class OHLCVSummary(SerializableModel):
    timestamp_utc: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int


class TechnicalAnalysisInput(SerializableModel):
    symbol: str
    timeframe: Literal["15m"] = "15m"
    timestamp_utc: datetime
    last_price: float
    ohlcv_recent: list[OHLCVSummary]
    rsi_14: float | None
    macd: float | None
    macd_signal: float | None
    macd_histogram: float | None
    macd_histogram_last_3: list[float]
    ema_20: float | None
    ema_50: float | None
    vwap: float | None
    atr_14: float | None
    support_levels: list[float]
    resistance_levels: list[float]
    volume_regime: Literal["low", "normal", "high", "spike"]
    trend_regime: Literal["bullish", "bearish", "mixed"]
    technical_events: list[str]
    news_summary: list[str]


class NewsItem(SerializableModel):
    title: str
    source: str
    published_at: datetime
    summary: str
    url: HttpUrl | str
    related_symbols: list[str] = Field(default_factory=list)
    relevance_score: float = Field(default=0.0, ge=0.0, le=1.0)

    @field_validator("published_at")
    @classmethod
    def normalize_published_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class GeminiSignal(SerializableModel):
    symbol: str
    timestamp_utc: datetime
    analysis_level: Literal["json_only", "multimodal_review"]
    bias: Literal["bullish", "bearish", "neutral"]
    sentiment_score: float = Field(ge=-10, le=10)
    confidence: float = Field(ge=0, le=1)
    setup_type: Literal["bullish_reversal", "bearish_breakdown", "breakout", "breakdown", "no_trade"]
    visual_confirmation: Literal["confirmed", "rejected", "not_applicable"]
    should_alert: bool
    entry_zone: list[float] = Field(min_length=2, max_length=2)
    stop_loss: float
    take_profit: list[float] = Field(min_length=2, max_length=2)
    risk_reward_ratio: float = Field(ge=0)
    reasons: list[str]
    risk_warnings: list[str]

    @field_validator("timestamp_utc")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class AlertDecision(SerializableModel):
    symbol: str
    should_alert: bool
    reason: str
    severity: Literal["info", "watch", "strong"] = "info"
    timestamp_utc: datetime
    signal: GeminiSignal
    technical_snapshot: TechnicalSnapshot
