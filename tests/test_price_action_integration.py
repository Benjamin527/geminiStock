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
    sent_texts = []

    def fake_send_feishu_text(webhook_url, text):
        sent_texts.append(text)
        return True

    monkeypatch.setattr("gemini_stock.main.send_feishu_text", fake_send_feishu_text)
    settings = Settings(feishu_webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/token")
    db = Database(tmp_path / "signals.db")
    db.initialize()

    first = maybe_send_price_action_alerts(settings, db, "TSLL", _signal(), _candles(12.15), trading_date="2026-01-05")
    second = maybe_send_price_action_alerts(settings, db, "TSLL", _signal(), _candles(12.15), trading_date="2026-01-05")

    assert first == 1
    assert second == 0
    assert len(sent_texts) == 1
    assert "【价格到位】TSLL｜买入区" in sent_texts[0]
    assert db.has_alert_event("feishu", "price_action:TSLL:buy:2026-01-05:12.10-12.20") is True


def test_maybe_send_price_action_alerts_skips_benchmarks(tmp_path, monkeypatch):
    sent_texts = []
    monkeypatch.setattr("gemini_stock.main.send_feishu_text", lambda webhook_url, text: sent_texts.append(text) or True)
    settings = Settings(feishu_webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/token")
    db = Database(tmp_path / "signals.db")
    db.initialize()

    count = maybe_send_price_action_alerts(settings, db, "SPY", _signal().model_copy(update={"symbol": "SPY"}), _candles(12.15), trading_date="2026-01-05")

    assert count == 0
    assert sent_texts == []
