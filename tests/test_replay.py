from datetime import datetime, timedelta, timezone

from gemini_stock.replay import DailyReview, build_daily_review, format_daily_review, summarize_alert_outcomes
from gemini_stock.schemas import Candle, GeminiSignal
from gemini_stock.storage.db import Database


def test_summarize_alert_outcomes_calculates_forward_return(tmp_path):
    db = Database(tmp_path / "signals.db")
    db.initialize()
    start = datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc)
    db.save_alert(
        "TSLL",
        {
            "type": "primary_alert",
            "signal": {"bias": "bullish"},
            "technical_snapshot": {"close": 12.0},
        },
        "feishu",
    )
    with db.connect() as conn:
        conn.execute("UPDATE alerts SET created_at_utc = ?", (start.isoformat(),))
    db.save_candles(
        [
            Candle(symbol="TSLL", interval="15m", timestamp_utc=start + timedelta(minutes=15), open=12, high=12.6, low=11.9, close=12.5, volume=1000),
            Candle(symbol="TSLL", interval="15m", timestamp_utc=start + timedelta(minutes=30), open=12.5, high=12.7, low=12.2, close=12.6, volume=1000),
        ]
    )

    summary = summarize_alert_outcomes(db, symbols=["TSLL"], horizon_minutes=120)

    assert summary["evaluated_alerts"] == 1
    assert summary["avg_forward_return_pct"] > 0
    assert summary["hit_positive_pct"] == 100.0


def test_summarize_alert_outcomes_groups_by_setup_type(tmp_path):
    db = Database(tmp_path / "signals.db")
    db.initialize()
    start = datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc)
    db.save_alert(
        "TQQQ",
        {
            "type": "primary_alert",
            "signal": {"bias": "bullish", "setup_type": "breakout"},
            "technical_snapshot": {"close": 100.0},
        },
        "feishu",
    )
    db.save_alert(
        "TQQQ",
        {
            "type": "primary_alert",
            "signal": {"bias": "bullish", "setup_type": "bullish_reversal"},
            "technical_snapshot": {"close": 50.0},
        },
        "feishu",
    )
    with db.connect() as conn:
        conn.execute("UPDATE alerts SET created_at_utc = ? WHERE id = 1", (start.isoformat(),))
        conn.execute("UPDATE alerts SET created_at_utc = ? WHERE id = 2", ((start + timedelta(hours=4)).isoformat(),))
    db.save_candles(
        [
            Candle(symbol="TQQQ", interval="15m", timestamp_utc=start + timedelta(minutes=15), open=100, high=104, low=99, close=103, volume=1000),
            Candle(symbol="TQQQ", interval="15m", timestamp_utc=start + timedelta(minutes=30), open=103, high=104, low=102, close=104, volume=1000),
            Candle(symbol="TQQQ", interval="15m", timestamp_utc=start + timedelta(hours=4, minutes=15), open=50, high=50, low=48, close=49, volume=1000),
            Candle(symbol="TQQQ", interval="15m", timestamp_utc=start + timedelta(hours=4, minutes=30), open=49, high=49, low=47, close=48, volume=1000),
        ]
    )

    summary = summarize_alert_outcomes(db, symbols=["TQQQ"], horizon_minutes=120)

    assert summary["by_setup_type"]["breakout"]["evaluated_alerts"] == 1
    assert summary["by_setup_type"]["breakout"]["avg_forward_return_pct"] > 0
    assert summary["by_setup_type"]["bullish_reversal"]["evaluated_alerts"] == 1
    assert summary["by_setup_type"]["bullish_reversal"]["avg_forward_return_pct"] < 0


def _signal(symbol: str, timestamp: datetime, bias: str = "bullish") -> GeminiSignal:
    return GeminiSignal(
        symbol=symbol,
        timestamp_utc=timestamp,
        analysis_level="json_only",
        bias=bias,
        sentiment_score=6.4,
        confidence=0.7,
        setup_type="bullish_reversal",
        visual_confirmation="not_applicable",
        should_alert=True,
        entry_zone=[10.0, 10.2],
        stop_loss=9.8,
        take_profit=[10.8, 11.0],
        risk_reward_ratio=1.8,
        reasons=["等待回踩后确认"],
        risk_warnings=["不能追高"],
    )


def test_build_daily_review_scores_hits_and_misses_from_price_path(tmp_path):
    db = Database(tmp_path / "signals.db")
    db.initialize()
    trade_date = datetime(2026, 1, 5, tzinfo=timezone.utc).date()
    start = datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc)
    db.save_llm_output(
        "CONL",
        {"technical_snapshot": {"timestamp_utc": start.isoformat()}},
        _signal("CONL", start).json_dict(),
        None,
    )
    db.save_llm_output(
        "TSLL",
        {"technical_snapshot": {"timestamp_utc": start.isoformat()}},
        _signal("TSLL", start).json_dict(),
        None,
    )
    db.save_candles(
        [
            Candle(symbol="CONL", interval="1m", timestamp_utc=start, open=10, high=10.1, low=10.0, close=10.05, volume=1000),
            Candle(symbol="CONL", interval="1m", timestamp_utc=start + timedelta(minutes=30), open=10.2, high=10.9, low=10.1, close=10.85, volume=1200),
            Candle(symbol="TSLL", interval="1m", timestamp_utc=start, open=10.1, high=10.2, low=10.0, close=10.1, volume=1000),
            Candle(symbol="TSLL", interval="1m", timestamp_utc=start + timedelta(minutes=30), open=9.9, high=10.0, low=9.7, close=9.75, volume=1200),
        ]
    )

    review = build_daily_review(db, ["CONL", "TSLL"], trade_date)

    assert review.trading_date == "2026-01-05"
    assert review.evaluated_count == 2
    assert review.accuracy_pct == 50.0
    assert review.symbol_reviews[0].symbol == "CONL"
    assert review.symbol_reviews[0].outcome == "hit"
    assert review.symbol_reviews[1].symbol == "TSLL"
    assert review.symbol_reviews[1].outcome == "miss"
    assert any("二次握手" in note for note in review.learning_notes)


def test_format_daily_review_includes_score_and_learning_notes():
    review = DailyReview(
        trading_date="2026-01-05",
        evaluated_count=1,
        hit_count=1,
        accuracy_pct=100.0,
        symbol_reviews=[],
        learning_notes=["继续记录 L1 和二次握手，不追离 L1 太远的反弹。"],
    )

    text = format_daily_review(review)

    assert "【每日复盘】2026-01-05" in text
    assert "准确率：100.0%" in text
    assert "学习：" in text
    assert "结论：观察｜先记录 L1，等待二次握手，不为交易而交易。" in text
    assert len(text.splitlines()) <= 8
