from datetime import datetime, timedelta, timezone

import pandas as pd

from gemini_stock.features.analysis_input import build_analysis_input, is_strong_candidate
from gemini_stock.features.events import build_technical_events
from gemini_stock.features.snapshot import build_technical_snapshot
from gemini_stock.schemas import GeminiSignal


def _candles(rows: int = 40, base: float = 100) -> pd.DataFrame:
    start = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
    data = []
    for i in range(rows):
        close = base - i * 0.15
        data.append(
            {
                "timestamp": start + timedelta(minutes=15 * i),
                "open": close + 0.1,
                "high": close + 0.4,
                "low": close - 0.4,
                "close": close,
                "volume": 1000 + i * 20,
            }
        )
    return pd.DataFrame(data)


def _signal(score: float = 4, level: str = "json_only") -> GeminiSignal:
    return GeminiSignal(
        symbol="SPY",
        timestamp_utc=datetime.now(timezone.utc),
        analysis_level=level,
        bias="neutral",
        sentiment_score=score,
        confidence=0.5,
        setup_type="no_trade",
        visual_confirmation="not_applicable",
        should_alert=False,
        entry_zone=[0.0, 0.0],
        stop_loss=0.0,
        take_profit=[0.0, 0.0],
        risk_reward_ratio=0.0,
        reasons=[],
        risk_warnings=[],
    )


def test_analysis_input_contains_json_only_fields_and_no_image_path():
    df = _candles()
    snapshot = build_technical_snapshot("SPY", df)
    events = build_technical_events(df, snapshot)
    payload = build_analysis_input("SPY", df, snapshot, events, news_summary=[])
    data = payload.json_dict()

    assert data["symbol"] == "SPY"
    assert data["timeframe"] == "15m"
    assert data["last_price"] == snapshot.close
    assert len(data["ohlcv_recent"]) == 20
    assert "macd_histogram_last_3" in data
    assert "volume_regime" in data
    assert "trend_regime" in data
    assert data["technical_events"] == events
    assert "image_path" not in data


def test_technical_events_include_price_and_indicator_labels():
    df = _candles()
    snapshot = build_technical_snapshot("SPY", df)
    events = build_technical_events(df, snapshot)

    assert "price_below_ema20" in events
    assert "price_below_vwap" in events
    assert "price_below_ema50" in events


def test_strong_candidate_detects_level_one_score_or_extreme_rsi():
    df = _candles()
    snapshot = build_technical_snapshot("SPY", df)
    events = build_technical_events(df, snapshot)
    payload = build_analysis_input("SPY", df, snapshot, events, news_summary=[])

    assert is_strong_candidate(snapshot, payload, events, [], _signal(score=6.5)) is True
    assert is_strong_candidate(snapshot, payload, events, [], _signal(score=2)) is True
