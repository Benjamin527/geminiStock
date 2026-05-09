from datetime import datetime, timezone

from gemini_stock.main import build_decision_alert_event_key, should_send_decision_alert
from gemini_stock.schemas import AlertDecision, GeminiSignal, TechnicalSnapshot
from gemini_stock.storage.db import Database


def _decision() -> AlertDecision:
    signal = GeminiSignal(
        symbol="TQQQ",
        timestamp_utc=datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc),
        analysis_level="multimodal_review",
        bias="bullish",
        sentiment_score=6.4,
        confidence=0.7,
        setup_type="breakout",
        visual_confirmation="confirmed",
        should_alert=True,
        entry_zone=[62.69, 62.73],
        stop_loss=62.28,
        take_profit=[62.95, 63.15],
        risk_reward_ratio=1.02,
        reasons=["Breakout setup."],
        risk_warnings=["Fast moves can reverse."],
    )
    snapshot = TechnicalSnapshot(
        symbol="TQQQ",
        interval="15m",
        timestamp_utc=datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc),
        open=62.5,
        high=62.8,
        low=62.4,
        close=62.7,
        volume=1000,
        rsi_14=63.4,
        macd=1,
        macd_signal=0.5,
        macd_histogram=0.5,
        ema_20=62.4,
        ema_50=61.95,
        vwap=62.5,
        atr_14=0.22,
        recent_4h_high=63,
        recent_4h_low=61,
        support_levels=[61.5],
        resistance_levels=[63.0],
    )
    return AlertDecision(
        symbol="TQQQ",
        should_alert=True,
        reason="llm_alert",
        timestamp_utc=datetime(2026, 1, 5, 15, 2, tzinfo=timezone.utc),
        signal=signal,
        technical_snapshot=snapshot,
    )


def test_build_decision_alert_event_key_includes_zone_and_date():
    event_key = build_decision_alert_event_key(_decision(), trading_date="2026-01-05")

    assert event_key == "alert:TQQQ:llm_alert:2026-01-05:62.69-62.73:62.95-63.15"


def test_should_send_decision_alert_blocks_same_event_and_recent_type(tmp_path):
    db = Database(tmp_path / "signals.db")
    db.initialize()
    decision = _decision()
    event_key = build_decision_alert_event_key(decision, trading_date="2026-01-05")
    now = datetime.now(timezone.utc)
    db.save_alert("TQQQ", {"type": "primary_alert", "event_key": event_key}, "feishu")

    assert should_send_decision_alert(db, "feishu", decision, event_key, now=now, cooldown_minutes=60) is False
    assert should_send_decision_alert(db, "telegram", decision, event_key, now=now, cooldown_minutes=60) is True
