from datetime import datetime, timezone

import pandas as pd

from gemini_stock.main import (
    maybe_send_daily_review,
    maybe_send_opening_silence_self_check,
    run_maintenance_tasks,
)
from gemini_stock.config import Settings
from gemini_stock.schemas import Candle, GeminiSignal, TechnicalSnapshot
from gemini_stock.storage.db import Database


def _snapshot(symbol: str) -> TechnicalSnapshot:
    return TechnicalSnapshot(
        symbol=symbol,
        interval="15m",
        timestamp_utc=datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc),
        open=649,
        high=652,
        low=646,
        close=648,
        volume=1000,
        rsi_14=52,
        macd=1,
        macd_signal=0.5,
        macd_histogram=0.5,
        ema_20=646,
        ema_50=642,
        vwap=647,
        atr_14=8,
        recent_4h_high=654,
        recent_4h_low=638,
        support_levels=[642, 635, 628],
        resistance_levels=[650, 662, 670],
    )


def _signal(symbol: str) -> GeminiSignal:
    return GeminiSignal(
        symbol=symbol,
        timestamp_utc=datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc),
        analysis_level="json_only",
        bias="bullish",
        sentiment_score=5.0,
        confidence=0.7,
        setup_type="breakout",
        visual_confirmation="not_applicable",
        should_alert=False,
        entry_zone=[647, 649],
        stop_loss=642,
        take_profit=[662, 670],
        risk_reward_ratio=1.8,
        reasons=["test"],
        risk_warnings=["test"],
    )


def _save_worker_output(db: Database, symbol: str, created_at: str) -> None:
    db.save_llm_output(
        symbol,
        {"technical_snapshot": {"timestamp_utc": "2026-01-05T15:00:00+00:00"}},
        _signal(symbol).json_dict(),
        None,
    )
    with db.connect() as conn:
        conn.execute(
            """
            UPDATE llm_outputs
            SET created_at_utc = ?
            WHERE id = (SELECT id FROM llm_outputs ORDER BY id DESC LIMIT 1)
            """,
            (created_at,),
        )


def _save_primary_candle(db: Database, symbol: str, timestamp_utc: str, close: float = 10.1) -> None:
    candle_time = datetime.fromisoformat(timestamp_utc.replace("Z", "+00:00"))
    db.save_candles(
        [
            Candle(
                symbol=symbol,
                interval="1m",
                timestamp_utc=candle_time,
                open=close - 0.1,
                high=close + 0.1,
                low=close - 0.2,
                close=close,
                volume=1000,
            )
        ]
    )


def _save_recent_alert(db: Database, created_at: str) -> None:
    db.save_alert(
        "TSLL",
        {
            "event_key": "price_action_p2:TSLL:buy:2026-01-05:12.10-12.20",
            "type": "price_action_p2",
            "text": "recent alert",
        },
        "feishu",
    )
    with db.connect() as conn:
        conn.execute(
            """
            UPDATE alerts
            SET created_at_utc = ?
            WHERE id = (SELECT id FROM alerts ORDER BY id DESC LIMIT 1)
            """,
            (created_at,),
        )


def test_maybe_send_daily_review_sends_once_at_beijing_eight(monkeypatch, tmp_path):
    db = Database(tmp_path / "review.db")
    db.initialize()
    settings = Settings(
        feishu_webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/token",
        symbols=["CONL", "TSLL"],
    )
    start = datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc)
    db.save_llm_output(
        "CONL",
        {"technical_snapshot": {"timestamp_utc": start.isoformat()}},
        _signal("CONL").model_copy(update={"timestamp_utc": start}).json_dict(),
        None,
    )
    from gemini_stock.schemas import Candle

    db.save_candles(
        [
            Candle(symbol="CONL", interval="1m", timestamp_utc=start, open=10, high=10.1, low=10.0, close=10.05, volume=1000),
            Candle(symbol="CONL", interval="1m", timestamp_utc=start.replace(hour=16), open=10.2, high=10.9, low=10.1, close=10.85, volume=1200),
        ]
    )
    captured = {"calls": 0, "text": ""}

    def fake_send(webhook_url, text, now_fn=None, bypass_quiet_hours=False, **kwargs):
        captured["calls"] += 1
        captured["text"] = text
        return True

    monkeypatch.setattr("gemini_stock.main.send_feishu_text", fake_send)
    now = datetime.fromisoformat("2026-01-06T08:05:00+08:00")

    maybe_send_daily_review(settings, db, review_symbols=settings.symbols, now=now)
    maybe_send_daily_review(settings, db, review_symbols=settings.symbols, now=now)

    assert captured["calls"] == 1
    assert "【每日复盘】2026-01-05" in captured["text"]
    assert db.has_alert_event("feishu", "daily_review:2026-01-05") is True


def test_maybe_send_daily_review_uses_previous_us_trading_day_after_weekend(monkeypatch, tmp_path):
    db = Database(tmp_path / "review.db")
    db.initialize()
    settings = Settings(
        feishu_webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/token",
        symbols=["CONL"],
    )
    start = datetime(2026, 1, 9, 15, 0, tzinfo=timezone.utc)
    db.save_llm_output(
        "CONL",
        {"technical_snapshot": {"timestamp_utc": start.isoformat()}},
        _signal("CONL").model_copy(update={"timestamp_utc": start}).json_dict(),
        None,
    )
    from gemini_stock.schemas import Candle

    db.save_candles(
        [
            Candle(symbol="CONL", interval="1m", timestamp_utc=start, open=10, high=10.1, low=10.0, close=10.05, volume=1000),
            Candle(symbol="CONL", interval="1m", timestamp_utc=start.replace(hour=16), open=10.2, high=10.9, low=10.1, close=10.85, volume=1200),
        ]
    )
    captured = {"calls": 0, "text": ""}

    def fake_send(webhook_url, text, now_fn=None, bypass_quiet_hours=False, **kwargs):
        captured["calls"] += 1
        captured["text"] = text
        return True

    monkeypatch.setattr("gemini_stock.main.send_feishu_text", fake_send)
    now = datetime.fromisoformat("2026-01-12T08:05:00+08:00")

    maybe_send_daily_review(settings, db, review_symbols=settings.symbols, now=now)

    assert captured["calls"] == 1
    assert "【每日复盘】2026-01-09" in captured["text"]
    assert db.has_alert_event("feishu", "daily_review:2026-01-09") is True


def test_run_maintenance_tasks_can_send_daily_review_when_market_is_closed(monkeypatch, tmp_path):
    db = Database(tmp_path / "review.db")
    db.initialize()
    settings = Settings(
        database_path=db.path,
        feishu_webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/token",
        symbols=["CONL"],
    )
    start = datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc)
    db.save_llm_output(
        "CONL",
        {"technical_snapshot": {"timestamp_utc": start.isoformat()}},
        _signal("CONL").model_copy(update={"timestamp_utc": start}).json_dict(),
        None,
    )
    from gemini_stock.schemas import Candle

    db.save_candles(
        [
            Candle(symbol="CONL", interval="1m", timestamp_utc=start, open=10, high=10.1, low=10.0, close=10.05, volume=1000),
            Candle(symbol="CONL", interval="1m", timestamp_utc=start.replace(hour=16), open=10.2, high=10.9, low=10.1, close=10.85, volume=1200),
        ]
    )
    calls = {"count": 0}

    def fake_send(webhook_url, text, now_fn=None, bypass_quiet_hours=False, **kwargs):
        calls["count"] += 1
        return True

    monkeypatch.setattr("gemini_stock.main.send_feishu_text", fake_send)

    run_maintenance_tasks(settings, now=datetime.fromisoformat("2026-01-06T08:05:00+08:00"))

    assert calls["count"] == 1
    assert db.has_alert_event("feishu", "daily_review:2026-01-05") is True


def test_maybe_send_daily_review_still_sends_when_no_samples(monkeypatch, tmp_path):
    db = Database(tmp_path / "review.db")
    db.initialize()
    settings = Settings(
        feishu_webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/token",
        symbols=["CONL", "TSLL"],
    )
    captured = {"calls": 0, "text": ""}

    def fake_send(webhook_url, text, now_fn=None, bypass_quiet_hours=False, **kwargs):
        captured["calls"] += 1
        captured["text"] = text
        return True

    monkeypatch.setattr("gemini_stock.main.send_feishu_text", fake_send)
    now = datetime.fromisoformat("2026-01-06T08:05:00+08:00")

    maybe_send_daily_review(settings, db, review_symbols=settings.symbols, now=now)

    assert captured["calls"] == 1
    assert "无足够样本评分。" in captured["text"]
    assert db.has_alert_event("feishu", "daily_review:2026-01-05") is True


def test_opening_silence_self_check_skips_outside_opening_window(monkeypatch, tmp_path):
    db = Database(tmp_path / "selfcheck.db")
    db.initialize()
    settings = Settings(
        feishu_webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/token",
        symbols=["TSLL"],
    )
    sent_cards = []
    monkeypatch.setattr("gemini_stock.main.send_feishu_interactive_card", lambda webhook_url, card, **kwargs: sent_cards.append(card) or True)

    sent = maybe_send_opening_silence_self_check(
        settings,
        db,
        primary_symbols=["TSLL"],
        now=datetime.fromisoformat("2026-01-05T11:05:00-05:00"),
    )

    assert sent is False
    assert sent_cards == []


def test_opening_silence_self_check_skips_when_recent_alert_exists(monkeypatch, tmp_path):
    db = Database(tmp_path / "selfcheck.db")
    db.initialize()
    settings = Settings(
        feishu_webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/token",
        symbols=["TSLL"],
    )
    _save_recent_alert(db, "2026-01-05T14:50:00+00:00")
    sent_cards = []
    monkeypatch.setattr("gemini_stock.main.send_feishu_interactive_card", lambda webhook_url, card, **kwargs: sent_cards.append(card) or True)

    sent = maybe_send_opening_silence_self_check(
        settings,
        db,
        primary_symbols=["TSLL"],
        now=datetime.fromisoformat("2026-01-05T10:05:00-05:00"),
    )

    assert sent is False
    assert sent_cards == []


def test_opening_silence_self_check_skips_when_system_is_healthy(monkeypatch, tmp_path):
    db = Database(tmp_path / "selfcheck.db")
    db.initialize()
    settings = Settings(
        feishu_webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/token",
        symbols=["TSLL"],
        delayed_data_tolerance_minutes=30,
    )
    _save_worker_output(db, "TSLL", "2026-01-05T15:04:00+00:00")
    _save_primary_candle(db, "TSLL", "2026-01-05T15:03:00+00:00")
    sent_cards = []
    monkeypatch.setattr("gemini_stock.main.send_feishu_interactive_card", lambda webhook_url, card, **kwargs: sent_cards.append(card) or True)

    sent = maybe_send_opening_silence_self_check(
        settings,
        db,
        primary_symbols=["TSLL"],
        now=datetime.fromisoformat("2026-01-05T10:05:00-05:00"),
    )

    assert sent is False
    assert sent_cards == []


def test_opening_silence_self_check_sends_when_market_data_is_unhealthy(monkeypatch, tmp_path):
    db = Database(tmp_path / "selfcheck.db")
    db.initialize()
    settings = Settings(
        feishu_webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/token",
        symbols=["TSLL"],
        delayed_data_tolerance_minutes=30,
    )
    _save_worker_output(db, "TSLL", "2026-01-05T15:04:00+00:00")
    _save_primary_candle(db, "TSLL", "2026-01-05T14:20:00+00:00")
    sent_cards = []

    def fake_send(webhook_url, card, now_fn=None, bypass_quiet_hours=False, **kwargs):
        sent_cards.append({"card": card, "bypass_quiet_hours": bypass_quiet_hours})
        return True

    monkeypatch.setattr("gemini_stock.main.send_feishu_interactive_card", fake_send)

    sent = maybe_send_opening_silence_self_check(
        settings,
        db,
        primary_symbols=["TSLL"],
        now=datetime.fromisoformat("2026-01-05T10:05:00-05:00"),
    )

    assert sent is True
    assert len(sent_cards) == 1
    assert sent_cards[0]["bypass_quiet_hours"] is True
    assert "开盘静默 30 分钟" in sent_cards[0]["card"]["header"]["title"]["content"]
    assert db.has_alert_event("feishu", "self_check:opening_silence:2026-01-05:1000") is True


def test_opening_silence_self_check_deduplicates_within_bucket(monkeypatch, tmp_path):
    db = Database(tmp_path / "selfcheck.db")
    db.initialize()
    settings = Settings(
        feishu_webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/token",
        symbols=["TSLL"],
        delayed_data_tolerance_minutes=30,
    )
    _save_worker_output(db, "TSLL", "2026-01-05T15:04:00+00:00")
    _save_primary_candle(db, "TSLL", "2026-01-05T14:20:00+00:00")
    sent_cards = []
    monkeypatch.setattr("gemini_stock.main.send_feishu_interactive_card", lambda webhook_url, card, **kwargs: sent_cards.append(card) or True)

    first = maybe_send_opening_silence_self_check(
        settings,
        db,
        primary_symbols=["TSLL"],
        now=datetime.fromisoformat("2026-01-05T10:05:00-05:00"),
    )
    second = maybe_send_opening_silence_self_check(
        settings,
        db,
        primary_symbols=["TSLL"],
        now=datetime.fromisoformat("2026-01-05T10:20:00-05:00"),
    )

    assert first is True
    assert second is False
    assert len(sent_cards) == 1
