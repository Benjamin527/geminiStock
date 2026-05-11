from datetime import datetime, timezone

import pandas as pd

from gemini_stock.main import SymbolRunResult, maybe_send_daily_review, maybe_send_premarket_brief, run_maintenance_tasks
from gemini_stock.config import Settings
from gemini_stock.schemas import GeminiSignal, TechnicalSnapshot
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


def test_maybe_send_premarket_brief_sends_once(monkeypatch, tmp_path):
    db = Database(tmp_path / "brief.db")
    db.initialize()
    settings = Settings(
        feishu_webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/token",
        symbols=["TQQQ", "TSLL"],
        benchmark_symbols=["SPY", "QQQ"],
    )
    captured = {"calls": 0, "text": ""}

    def fake_send(webhook_url, text, now_fn=None):
        captured["calls"] += 1
        captured["text"] = text
        return True

    monkeypatch.setattr("gemini_stock.main.send_feishu_text", fake_send)

    results = [
        SymbolRunResult("SPY", "benchmark", _snapshot("SPY"), _signal("SPY"), pd.DataFrame()),
        SymbolRunResult("QQQ", "benchmark", _snapshot("QQQ"), _signal("QQQ"), pd.DataFrame()),
    ]
    now = datetime(2026, 1, 5, 8, 40, tzinfo=timezone.utc).astimezone()
    # convert to explicit New York local timestamp
    now = datetime(2026, 1, 5, 8, 40).astimezone()

    # use fixed NY premarket time with offset included
    now = datetime.fromisoformat("2026-01-05T08:40:00-05:00")

    maybe_send_premarket_brief(settings, db, results, now=now)
    maybe_send_premarket_brief(settings, db, results, now=now)

    assert captured["calls"] == 1
    assert "【盘前观察】2026-01-05" in captured["text"]
    assert db.has_alert_event("feishu", "premarket_briefing:2026-01-05") is True


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

    def fake_send(webhook_url, text, now_fn=None, bypass_quiet_hours=False):
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

    def fake_send(webhook_url, text, now_fn=None, bypass_quiet_hours=False):
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

    def fake_send(webhook_url, text, now_fn=None, bypass_quiet_hours=False):
        captured["calls"] += 1
        captured["text"] = text
        return True

    monkeypatch.setattr("gemini_stock.main.send_feishu_text", fake_send)
    now = datetime.fromisoformat("2026-01-06T08:05:00+08:00")

    maybe_send_daily_review(settings, db, review_symbols=settings.symbols, now=now)

    assert captured["calls"] == 1
    assert "无足够样本评分。" in captured["text"]
    assert db.has_alert_event("feishu", "daily_review:2026-01-05") is True
