from datetime import datetime, timezone

from gemini_stock.schemas import GeminiSignal, NewsItem, TechnicalSnapshot
from gemini_stock.storage.db import Database


def test_database_persists_features_news_llm_outputs_and_alerts(tmp_path):
    db = Database(tmp_path / "signals.db")
    db.initialize()

    snapshot = TechnicalSnapshot(
        symbol="SPY",
        interval="15m",
        timestamp_utc=datetime.now(timezone.utc),
        open=99,
        high=101,
        low=98,
        close=100,
        volume=1000,
        rsi_14=31,
        macd=1,
        macd_signal=0.5,
        macd_histogram=0.5,
        ema_20=100,
        ema_50=99,
        vwap=99.5,
        atr_14=2,
        recent_4h_high=103,
        recent_4h_low=97,
        support_levels=[97, 98],
        resistance_levels=[102, 103],
    )
    news = NewsItem(
        title="ETF inflows rise",
        source="unit-test",
        published_at=datetime.now(timezone.utc),
        summary="SPY and QQQ saw inflows.",
        url="https://example.com/news",
        related_symbols=["SPY", "QQQ"],
        relevance_score=0.8,
    )

    db.save_feature(snapshot)
    db.save_news([news])
    db.save_llm_output("SPY", {"prompt": "x"}, {"symbol": "SPY"}, None)
    db.save_alert("SPY", {"message": "alert"}, "telegram")

    assert db.count("features") == 1
    assert db.count("news") == 1
    assert db.count("llm_outputs") == 1
    assert db.count("alerts") == 1


def _signal(timestamp: datetime) -> GeminiSignal:
    return GeminiSignal(
        symbol="TSLL",
        timestamp_utc=timestamp,
        analysis_level="json_only",
        bias="bullish",
        sentiment_score=6.4,
        confidence=0.7,
        setup_type="breakout",
        visual_confirmation="not_applicable",
        should_alert=True,
        entry_zone=[12.10, 12.20],
        stop_loss=11.90,
        take_profit=[12.60, 12.80],
        risk_reward_ratio=1.5,
        reasons=["Price is near entry."],
        risk_warnings=["Fast moves can reverse."],
    )


def test_database_finds_recent_alert_type(tmp_path):
    db = Database(tmp_path / "signals.db")
    db.initialize()
    now = datetime.now(timezone.utc)
    db.save_alert("TSLL", {"type": "primary_alert", "event_key": "alert:TSLL:2026-01-05"}, "feishu")

    assert db.has_recent_alert_type("TSLL", "feishu", "primary_alert", now=now, within_minutes=60) is True
    assert db.has_recent_alert_type("TQQQ", "feishu", "primary_alert", now=now, within_minutes=60) is False


def test_database_loads_successful_signal_for_same_snapshot_timestamp(tmp_path):
    db = Database(tmp_path / "signals.db")
    db.initialize()
    snapshot_time = datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc)
    signal = _signal(snapshot_time)
    db.save_llm_output(
        "TSLL",
        {"technical_snapshot": {"timestamp_utc": snapshot_time.isoformat()}},
        signal.json_dict(),
        None,
    )

    loaded = db.get_successful_signal_for_snapshot("TSLL", snapshot_time)

    assert loaded is not None
    assert loaded.symbol == "TSLL"
    assert loaded.entry_zone == [12.10, 12.20]


def test_database_promotes_alert_event_key_to_indexed_column(tmp_path):
    db = Database(tmp_path / "signals.db")
    db.initialize()
    db.save_alert("TSLL", {"type": "primary_alert", "event_key": "alert:TSLL:indexed"}, "feishu")

    with db.connect() as conn:
        row = conn.execute("SELECT event_key, alert_type FROM alerts WHERE symbol = ?", ("TSLL",)).fetchone()

    assert row["event_key"] == "alert:TSLL:indexed"
    assert row["alert_type"] == "primary_alert"


def test_database_migrates_existing_alert_payload_metadata(tmp_path):
    db_path = tmp_path / "signals.db"
    db = Database(db_path)
    db.initialize()
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO alerts (symbol, channel, payload_json) VALUES (?, ?, ?)",
            ("TSLL", "feishu", '{"type":"price_action","event_key":"price_action:old"}'),
        )
    db.initialize()

    with db.connect() as conn:
        row = conn.execute("SELECT event_key, alert_type FROM alerts WHERE symbol = ?", ("TSLL",)).fetchone()

    assert row["event_key"] == "price_action:old"
    assert row["alert_type"] == "price_action"


def test_database_watchlist_can_append_and_list_symbols(tmp_path):
    db = Database(tmp_path / "signals.db")
    db.initialize()

    db.add_watch_symbol("conl", profile="primary")
    db.add_watch_symbol("TSLL", profile="primary")
    db.add_watch_symbol("QQQ", profile="benchmark")

    assert db.list_watch_symbols("primary") == ["CONL", "TSLL"]
    assert db.list_watch_symbols("benchmark") == ["QQQ"]


def test_database_watchlist_can_disable_existing_symbol(tmp_path):
    db = Database(tmp_path / "signals.db")
    db.initialize()

    db.add_watch_symbol("CRCL", profile="primary")

    removed = db.remove_watch_symbol("crcl", profile="primary")

    assert removed is True
    assert db.list_watch_symbols("primary") == []


def test_database_connect_enables_wal_and_busy_timeout(tmp_path):
    db = Database(tmp_path / "signals.db")
    db.initialize()

    with db.connect() as conn:
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]

    assert str(journal_mode).lower() == "wal"
    assert int(busy_timeout) >= 5000
