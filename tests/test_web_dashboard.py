from datetime import datetime, timezone

from fastapi.testclient import TestClient

from gemini_stock.schemas import Candle, GeminiSignal, TechnicalSnapshot
from gemini_stock.storage.db import Database
from gemini_stock.web.app import create_app
from gemini_stock.web import repository as dashboard_repository
from gemini_stock.web.repository import DashboardRepository, to_beijing_time


def _snapshot() -> TechnicalSnapshot:
    return TechnicalSnapshot(
        symbol="SPY",
        interval="15m",
        timestamp_utc=datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc),
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


def _signal() -> GeminiSignal:
    return GeminiSignal(
        symbol="SPY",
        timestamp_utc=datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc),
        analysis_level="json_only",
        bias="bullish",
        sentiment_score=6.8,
        confidence=0.7,
        setup_type="bullish_reversal",
        visual_confirmation="not_applicable",
        should_alert=False,
        entry_zone=[99, 100],
        stop_loss=96,
        take_profit=[104, 106],
        risk_reward_ratio=1.6,
        reasons=["test"],
        risk_warnings=["test"],
    )


def _quote_snapshot() -> dict:
    return {
        "regular_market_price": 101.25,
        "post_market_price": 101.6,
        "regular_market_time": "2026-01-05T21:00:00Z",
        "post_market_time": "2026-01-05T23:01:00Z",
        "source": "yfinance_quote",
    }


def _bearish_signal() -> GeminiSignal:
    return GeminiSignal(
        symbol="TSLL",
        timestamp_utc=datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc),
        analysis_level="multimodal_review",
        bias="bearish",
        sentiment_score=-5.0,
        confidence=0.7,
        setup_type="bearish_breakdown",
        visual_confirmation="confirmed",
        should_alert=False,
        entry_zone=[12.14, 12.16],
        stop_loss=12.27,
        take_profit=[12.09, 11.96],
        risk_reward_ratio=1.69,
        reasons=["test"],
        risk_warnings=["test"],
    )


def test_dashboard_repository_returns_latest_symbol_state(tmp_path):
    db = Database(tmp_path / "dashboard.db")
    db.initialize()
    db.save_feature(_snapshot())
    db.save_candles([
        Candle(
            symbol="SPY",
            interval="1m",
            timestamp_utc=datetime(2026, 1, 5, 21, 0, tzinfo=timezone.utc),
            open=101,
            high=101.5,
            low=100.8,
            close=101.25,
            volume=1000,
        )
    ])
    db.save_llm_output(
        "SPY",
        {"analysis_level": "json_only", "has_image": False, "technical_events": ["rsi_oversold"]},
        _signal().json_dict(),
        None,
    )

    state = DashboardRepository(db.path, tmp_path).get_symbol_states(["SPY"])

    assert state[0]["symbol"] == "SPY"
    assert state[0]["last_price"] == 101.25
    assert state[0]["regular_market_price"] == 101.25
    assert state[0]["post_market_price"] is None
    assert state[0]["regular_market_time"] == "2026-01-06 05:00:00 北京时间"
    assert state[0]["post_market_time"] is None
    assert state[0]["price_source"] == "stored_1m_candle"
    assert state[0]["analysis_level"] == "json_only"
    assert state[0]["has_image"] is False
    assert state[0]["sentiment_score"] == 6.8
    assert state[0]["technical_events"] == ["rsi_oversold"]
    assert state[0]["bias"] == "bullish"
    assert state[0]["entry_zone"] == [99, 100]
    assert state[0]["stop_loss"] == 96
    assert state[0]["take_profit"] == [104, 106]


def test_dashboard_repository_does_not_fetch_live_quotes_by_default(tmp_path, monkeypatch):
    def fail_live_quote(symbol):
        raise AssertionError("dashboard should not call live quotes")

    monkeypatch.setattr(dashboard_repository, "_fetch_quote_snapshot", fail_live_quote)
    db = Database(tmp_path / "dashboard.db")
    db.initialize()
    db.save_feature(_snapshot())

    state = DashboardRepository(db.path, tmp_path).get_symbol_states(["SPY"])

    assert state[0]["last_price"] == 100
    assert state[0]["price_source"] == "feature_snapshot"


def test_dashboard_page_shows_direction_and_targets_on_symbol_cards(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard_repository, "_fetch_quote_snapshot", lambda symbol: _quote_snapshot())
    db = Database(tmp_path / "dashboard.db")
    db.initialize()
    db.save_feature(_snapshot())
    db.save_llm_output("SPY", {"analysis_level": "json_only", "has_image": False}, _signal().json_dict(), None)
    app = create_app(database_path=db.path, chart_dir=tmp_path, symbols=["SPY"], benchmark_symbols=[])

    response = TestClient(app).get("/")

    assert "看多" in response.text
    assert "建议动作" in response.text
    assert "买入关注" in response.text
    assert "买入参考" in response.text
    assert "止损位" in response.text
    assert "卖出参考" in response.text
    assert "主交易收盘" in response.text
    assert "盘后价" in response.text


def test_dashboard_page_uses_long_only_labels_for_bearish_setups(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard_repository, "_fetch_quote_snapshot", lambda symbol: _quote_snapshot())
    db = Database(tmp_path / "dashboard.db")
    db.initialize()
    db.save_feature(_snapshot().model_copy(update={"symbol": "TSLL", "close": 12.15}))
    db.save_llm_output("TSLL", {"analysis_level": "multimodal_review", "has_image": True}, _bearish_signal().json_dict(), None)
    app = create_app(database_path=db.path, chart_dir=tmp_path, symbols=["TSLL"])

    response = TestClient(app).get("/")

    assert "建议动作" in response.text
    assert "减仓卖出" in response.text
    assert "卖出参考" in response.text
    assert "回补买入" in response.text
    assert "风险位" in response.text
    assert "12.14 - 12.16" in response.text
    assert "11.96 - 12.09" in response.text


def test_dashboard_hides_non_actionable_trade_ranges(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard_repository, "_fetch_quote_snapshot", lambda symbol: _quote_snapshot())
    db = Database(tmp_path / "dashboard.db")
    db.initialize()
    db.save_feature(_snapshot())
    db.save_llm_output(
        "SPY",
        {"analysis_level": "json_only", "has_image": False},
        _signal().model_copy(
            update={
                "setup_type": "no_trade",
                "entry_zone": [15.56, 15.58],
                "stop_loss": 15.55,
                "take_profit": [15.59, 15.61],
                "risk_reward_ratio": 0.4,
            }
        ).json_dict(),
        None,
    )
    app = create_app(database_path=db.path, chart_dir=tmp_path, symbols=["SPY"], benchmark_symbols=[])

    response = TestClient(app).get("/")

    assert "15.56 - 15.58" not in response.text
    assert "15.59 - 15.61" not in response.text


def test_dashboard_repository_returns_today_metrics(tmp_path):
    db = Database(tmp_path / "dashboard.db")
    db.initialize()
    db.save_feature(_snapshot())
    db.save_llm_output(
        "SPY",
        {"analysis_level": "json_only", "has_image": False, "technical_events": ["rsi_oversold"]},
        _signal().json_dict(),
        None,
    )
    db.save_llm_output(
        "SPY",
        {"analysis_level": "multimodal_review", "has_image": True, "technical_events": ["rsi_oversold"]},
        _signal().model_copy(update={"analysis_level": "multimodal_review", "visual_confirmation": "confirmed"}).json_dict(),
        None,
    )

    metrics = DashboardRepository(db.path, tmp_path).get_today_metrics(["SPY"])

    assert metrics["symbol_scans_today"] == 1
    assert metrics["estimated_run_cycles_today"] == 1
    assert metrics["json_only_calls_today"] == 1
    assert metrics["multimodal_calls_today"] == 1
    assert metrics["llm_errors_today"] == 0


def test_dashboard_page_renders_core_sections(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard_repository, "_fetch_quote_snapshot", lambda symbol: _quote_snapshot())
    db = Database(tmp_path / "dashboard.db")
    db.initialize()
    db.save_feature(_snapshot())
    db.save_llm_output("SPY", {"analysis_level": "json_only", "has_image": False}, _signal().json_dict(), None)
    app = create_app(database_path=db.path, chart_dir=tmp_path, symbols=["SPY"], benchmark_symbols=[])

    response = TestClient(app).get("/")

    assert response.status_code == 200
    assert "美股 AI 盯盘控制台" in response.text
    assert "SPY" in response.text
    assert "最近执行时间线" in response.text
    assert "今日运行概览" in response.text
    assert "距离下次执行" in response.text


def test_dashboard_benchmark_cards_show_regular_session_forecast(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard_repository, "_fetch_quote_snapshot", lambda symbol: _quote_snapshot())
    db = Database(tmp_path / "dashboard.db")
    db.initialize()
    db.save_feature(_snapshot())
    db.save_llm_output("SPY", {"analysis_level": "json_only", "has_image": False}, _signal().json_dict(), None)
    app = create_app(database_path=db.path, chart_dir=tmp_path, symbols=[], benchmark_symbols=["SPY"])

    response = TestClient(app).get("/")
    payload = TestClient(app).get("/api/status").json()

    assert response.status_code == 200
    assert "常规盘区间" in response.text
    assert "走强触发" in response.text
    assert "走弱触发" in response.text
    assert "短线" not in response.text
    assert payload["benchmarks"][0]["day_range"]


def test_dashboard_recent_sections_only_show_selected_symbols(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard_repository, "_fetch_quote_snapshot", lambda symbol: _quote_snapshot())
    db = Database(tmp_path / "dashboard.db")
    db.initialize()
    db.save_feature(_snapshot())
    db.save_llm_output("SPY", {"analysis_level": "json_only", "has_image": False}, _signal().json_dict(), None)
    db.save_llm_output("QQQ", {"analysis_level": "json_only", "has_image": False}, _signal().model_copy(update={"symbol": "QQQ"}).json_dict(), None)
    app = create_app(database_path=db.path, chart_dir=tmp_path, symbols=["SPY"], benchmark_symbols=[])

    response = TestClient(app).get("/")
    payload = TestClient(app).get("/api/status").json()

    assert "QQQ" not in response.text
    assert all(item["symbol"] == "SPY" for item in payload["recent_llm_outputs"])


def test_dashboard_displays_beijing_time(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard_repository, "_fetch_quote_snapshot", lambda symbol: _quote_snapshot())
    db = Database(tmp_path / "dashboard.db")
    db.initialize()
    db.save_feature(_snapshot())
    db.save_llm_output("SPY", {"analysis_level": "json_only", "has_image": False}, _signal().json_dict(), None)
    app = create_app(database_path=db.path, chart_dir=tmp_path, symbols=["SPY"])

    response = TestClient(app).get("/")
    payload = TestClient(app).get("/api/status").json()

    assert "2026-01-05 23:00:00 北京时间" in response.text
    assert payload["symbols"][0]["feature_timestamp"] == "2026-01-05 23:00:00 北京时间"


def test_dashboard_renders_benchmark_overview_section(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard_repository, "_fetch_quote_snapshot", lambda symbol: _quote_snapshot())
    db = Database(tmp_path / "dashboard.db")
    db.initialize()
    db.save_feature(_snapshot().model_copy(update={"symbol": "SPY", "close": 100.5, "support_levels": [99.8, 99.0], "resistance_levels": [101.2, 102.0]}))
    db.save_llm_output(
        "SPY",
        {"analysis_level": "json_only", "has_image": False},
        _signal().model_copy(update={"symbol": "SPY", "bias": "bullish", "sentiment_score": 4.5, "confidence": 0.65}).json_dict(),
        None,
    )
    app = create_app(database_path=db.path, chart_dir=tmp_path, symbols=["TSLL"], benchmark_symbols=["SPY"])

    response = TestClient(app).get("/")
    payload = TestClient(app).get("/api/status").json()

    assert "大盘观察" in response.text
    assert "常规盘区间" in response.text
    assert "走弱触发" in response.text
    assert "走强触发" in response.text
    assert payload["benchmarks"][0]["symbol"] == "SPY"


def test_dashboard_api_can_add_watchlist_symbol(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard_repository, "_fetch_quote_snapshot", lambda symbol: _quote_snapshot())
    db = Database(tmp_path / "dashboard.db")
    db.initialize()
    app = create_app(database_path=db.path, chart_dir=tmp_path)
    client = TestClient(app)

    response = client.post("/api/watchlist", json={"symbol": "nvda"})
    watchlist = client.get("/api/watchlist").json()["watchlist"]["primary"]

    assert response.status_code == 200
    assert "NVDA" in watchlist


def test_to_beijing_time_handles_sqlite_utc_suffix():
    assert to_beijing_time("2026-04-25T16:28:40.101Z") == "2026-04-26 00:28:40 北京时间"
