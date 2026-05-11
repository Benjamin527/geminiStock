from datetime import datetime, timezone

import pandas as pd

from gemini_stock.config import Settings
from gemini_stock.main import maybe_send_price_action_alerts
from gemini_stock.schemas import GeminiSignal
from gemini_stock.storage.db import Database


def _signal() -> GeminiSignal:
    return GeminiSignal(
        symbol="TSLL",
        timestamp_utc=datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc),
        analysis_level="multimodal_review",
        bias="bullish",
        sentiment_score=6.4,
        confidence=0.7,
        setup_type="breakout",
        visual_confirmation="confirmed",
        should_alert=True,
        entry_zone=[12.10, 12.20],
        stop_loss=11.90,
        take_profit=[12.60, 12.80],
        risk_reward_ratio=1.5,
        reasons=["Price is near entry."],
        risk_warnings=["Fast moves can reverse."],
    )


def _candles(price: float) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "timestamp": datetime(2026, 1, 5, 15, 1, tzinfo=timezone.utc),
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": 1000,
            }
        ]
    )


def test_maybe_send_price_action_alerts_sends_and_deduplicates_feishu(tmp_path, monkeypatch):
    sent_cards = []

    def fake_send_feishu_card(webhook_url, card, **kwargs):
        sent_cards.append(card)
        return True

    monkeypatch.setattr("gemini_stock.main.send_feishu_interactive_card", fake_send_feishu_card)
    monkeypatch.setattr("gemini_stock.main.send_feishu_text", lambda webhook_url, text, **kwargs: True)
    settings = Settings(feishu_webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/token")
    db = Database(tmp_path / "signals.db")
    db.initialize()

    first = maybe_send_price_action_alerts(settings, db, "TSLL", _signal(), _candles(12.15), trading_date="2026-01-05")
    second = maybe_send_price_action_alerts(settings, db, "TSLL", _signal(), _candles(12.15), trading_date="2026-01-05")

    assert first == 1
    assert second == 0
    assert len(sent_cards) == 1
    assert "P2 一般买入" in sent_cards[0]["header"]["title"]["content"]
    assert db.has_alert_event("feishu", "price_action_p2:TSLL:buy:2026-01-05:12.10-12.20") is True


def test_maybe_send_price_action_alerts_skips_benchmarks(tmp_path, monkeypatch):
    sent_texts = []
    monkeypatch.setattr("gemini_stock.main.send_feishu_text", lambda webhook_url, text, **kwargs: sent_texts.append(text) or True)
    settings = Settings(feishu_webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/token")
    db = Database(tmp_path / "signals.db")
    db.initialize()

    count = maybe_send_price_action_alerts(settings, db, "SPY", _signal().model_copy(update={"symbol": "SPY"}), _candles(12.15), trading_date="2026-01-05")

    assert count == 0
    assert sent_texts == []


def test_maybe_send_price_action_alerts_uses_bypass_for_high_priority(tmp_path, monkeypatch):
    captured = {"bypass": None, "title": None}

    def fake_send_feishu_card(webhook_url, card, **kwargs):
        captured["bypass"] = kwargs.get("bypass_quiet_hours")
        captured["title"] = card["header"]["title"]["content"]
        return True

    monkeypatch.setattr("gemini_stock.main.send_feishu_interactive_card", fake_send_feishu_card)
    monkeypatch.setattr("gemini_stock.main.send_feishu_text", lambda webhook_url, text, **kwargs: True)
    settings = Settings(feishu_webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/token")
    db = Database(tmp_path / "signals.db")
    db.initialize()
    signal = _signal().model_copy(update={"sentiment_score": 8.0, "confidence": 0.76, "risk_reward_ratio": 2.0})

    count = maybe_send_price_action_alerts(settings, db, "TSLL", signal, _candles(12.15), trading_date="2026-01-05")

    assert count == 1
    assert captured["bypass"] is True
    assert "P1 特别推荐买入" in captured["title"]
