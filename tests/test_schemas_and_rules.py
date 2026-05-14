from datetime import datetime, timezone

import pandas as pd
import pytest
from pydantic import ValidationError

from gemini_stock.config import Settings
from gemini_stock.rules.alert_rules import AlertRuleEngine
from gemini_stock.schemas import GeminiSignal, TechnicalSnapshot


def _technical(rsi: float = 30, close: float = 100, ema50: float = 99, atr: float = 2):
    return TechnicalSnapshot(
        symbol="SPY",
        interval="15m",
        timestamp_utc=datetime.now(timezone.utc),
        open=99,
        high=101,
        low=98,
        close=close,
        volume=1000,
        rsi_14=rsi,
        macd=1,
        macd_signal=0.5,
        macd_histogram=0.5,
        ema_20=100,
        ema_50=ema50,
        vwap=99.5,
        atr_14=atr,
        recent_4h_high=103,
        recent_4h_low=97,
        support_levels=[97, 98],
        resistance_levels=[102, 103],
    )


def _signal(score: float = 8, confidence: float = 0.7, setup_type: str = "bullish_reversal"):
    return GeminiSignal(
        symbol="SPY",
        timestamp_utc=datetime.now(timezone.utc),
        analysis_level="multimodal_review",
        bias="bullish",
        sentiment_score=score,
        confidence=confidence,
        setup_type=setup_type,
        visual_confirmation="confirmed",
        should_alert=True,
        entry_zone=[99, 100],
        stop_loss=96,
        take_profit=[105, 106],
        risk_reward_ratio=2,
        reasons=["RSI is oversold while price is holding near support."],
        risk_warnings=["News flow can reverse quickly."],
    )


def test_gemini_signal_rejects_out_of_range_score():
    with pytest.raises(ValidationError):
        _signal(score=11)


def test_default_primary_symbols_are_conl_and_tsll(monkeypatch):
    monkeypatch.delenv("SYMBOLS", raising=False)

    assert Settings(_env_file=None).symbols == ["CONL", "TSLL"]


def test_default_movement_alert_symbols_include_btc_only_for_drop_alerts(monkeypatch):
    monkeypatch.delenv("MOVEMENT_ALERT_SYMBOLS", raising=False)

    assert Settings(_env_file=None).movement_alert_symbols == ["BTC-USD"]


def test_alert_rule_marks_strong_buy_alert_reason():
    engine = AlertRuleEngine(cooldown_minutes=60)

    decision = engine.evaluate(_signal(), _technical())

    assert decision.should_alert is True
    assert decision.reason == "buy_alert"
    assert decision.severity == "strong"


def test_alert_rule_allows_llm_requested_primary_alerts_without_old_buy_thresholds():
    engine = AlertRuleEngine(cooldown_minutes=60)
    now = datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc)
    cases = [
        _signal(score=3.8, confidence=0.67, setup_type="breakout").model_copy(
            update={"symbol": "TQQQ", "risk_reward_ratio": 1.15, "should_alert": True}
        ),
        _signal(score=0.6, confidence=0.66, setup_type="no_trade").model_copy(
            update={"symbol": "TSLL", "risk_reward_ratio": 0.0, "bias": "neutral", "should_alert": True}
        ),
    ]

    decisions = [
        engine.evaluate(signal, _technical(rsi=50).model_copy(update={"symbol": signal.symbol}), now=now)
        for signal in cases
    ]

    assert [decision.symbol for decision in decisions] == ["TQQQ", "TSLL"]
    assert all(decision.should_alert for decision in decisions)
    assert [decision.severity for decision in decisions] == ["watch", "info"]


def test_alert_rule_blocks_when_price_too_far_below_ema50():
    engine = AlertRuleEngine(cooldown_minutes=60)

    decision = engine.evaluate(_signal(), _technical(close=90, ema50=100, atr=2))

    assert decision.should_alert is False
    assert "below EMA50" in decision.reason


def test_alert_rule_enforces_cooldown():
    engine = AlertRuleEngine(cooldown_minutes=60)
    now = datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc)

    first = engine.evaluate(_signal(), _technical(), now=now)
    second = engine.evaluate(_signal(), _technical(), now=now)

    assert first.should_alert is True
    assert second.should_alert is False
    assert second.reason == "cooldown_active"


def test_alert_rule_cooldown_is_scoped_to_event_family():
    engine = AlertRuleEngine(cooldown_minutes=60)
    now = datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc)
    buy_signal = _signal().model_copy(update={"symbol": "TSLL", "sentiment_score": 8.0, "confidence": 0.75, "risk_reward_ratio": 2.0})
    llm_signal = _signal(score=3.2, confidence=0.67, setup_type="breakout").model_copy(
        update={"symbol": "TSLL", "risk_reward_ratio": 1.1}
    )
    technical = _technical().model_copy(update={"symbol": "TSLL"})

    first = engine.evaluate(buy_signal, technical, now=now)
    second = engine.evaluate(llm_signal, technical, now=now)

    assert first.should_alert is True
    assert first.reason == "buy_alert"
    assert second.should_alert is True
    assert second.reason == "llm_alert"


def _one_minute_frame(prices: list[float]) -> pd.DataFrame:
    start = datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc)
    rows = []
    for idx, price in enumerate(prices):
        rows.append(
            {
                "timestamp": start.replace(minute=idx),
                "open": price,
                "high": price + 0.05,
                "low": price - 0.05,
                "close": price,
                "volume": 1000 + idx,
            }
        )
    return pd.DataFrame(rows)

