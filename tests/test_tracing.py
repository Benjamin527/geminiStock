from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from gemini_stock.config import Settings
from gemini_stock.main import RuntimeContext, _tracing_env_configured, run_once, run_symbol
from gemini_stock.rules.alert_rules import AlertRuleEngine
from gemini_stock.schemas import GeminiSignal


class FakeSpan:
    def __init__(self, tracer, name: str, service: str | None = None):
        self.tracer = tracer
        self.name = name
        self.service = service
        self.tags: dict[str, object] = {}
        self.error: str | None = None

    def __enter__(self):
        self.tracer.spans.append(self)
        self.tracer.stack.append(self)
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc is not None:
            self.error = str(exc)
        self.tracer.stack.pop()
        return False

    def set_tag(self, key: str, value: object):
        self.tags[key] = value


class FakeTracer:
    def __init__(self):
        self.spans: list[FakeSpan] = []
        self.stack: list[FakeSpan] = []

    def trace(self, name: str, service: str | None = None):
        return FakeSpan(self, name, service=service)


def _candles(rows: int = 40, interval_minutes: int = 15) -> pd.DataFrame:
    start = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
    data = []
    for i in range(rows):
        close = 100 + i * 0.1
        data.append(
            {
                "timestamp": start + timedelta(minutes=interval_minutes * i),
                "open": close - 0.05,
                "high": close + 0.1,
                "low": close - 0.1,
                "close": close,
                "volume": 1000 + i,
            }
        )
    return pd.DataFrame(data)


def _signal(symbol: str, timestamp: datetime, should_alert: bool = False) -> GeminiSignal:
    return GeminiSignal(
        symbol=symbol,
        timestamp_utc=timestamp,
        analysis_level="json_only",
        bias="bullish",
        sentiment_score=6.4,
        confidence=0.7,
        setup_type="breakout",
        visual_confirmation="not_applicable",
        should_alert=should_alert,
        entry_zone=[103.8, 104.0],
        stop_loss=102.0,
        take_profit=[105.0, 106.0],
        risk_reward_ratio=1.5,
        reasons=["Existing signal."],
        risk_warnings=["Fast moves can reverse."],
    )


def test_run_once_trace_wraps_cycle_and_children(monkeypatch, tmp_path):
    tracer = FakeTracer()
    monkeypatch.setattr("gemini_stock.main.tracer", tracer, raising=False)

    class FakeDatabase:
        def __init__(self, path):
            self.path = path

        def initialize(self):
            return None

        def list_watch_symbols(self, profile: str):
            return []

    symbol_runs: list[str] = []
    helper_calls: list[str] = []
    monkeypatch.setattr("gemini_stock.main.Database", FakeDatabase)
    monkeypatch.setattr(
        "gemini_stock.main.run_symbol",
        lambda symbol, settings, db, rules, profile="primary", context=None: symbol_runs.append(f"{profile}:{symbol}") or None,
    )
    monkeypatch.setattr(
        "gemini_stock.main.run_movement_only_once",
        lambda settings, db=None, context=None, exclude_symbols=None: helper_calls.append("movement") or 0,
    )
    monkeypatch.setattr(
        "gemini_stock.main.maybe_send_premarket_brief",
        lambda settings, db, benchmark_results: helper_calls.append("premarket"),
    )
    monkeypatch.setattr(
        "gemini_stock.main.run_maintenance_tasks",
        lambda settings, db=None, now=None, review_symbols=None: helper_calls.append("maintenance"),
    )
    monkeypatch.setattr(
        "gemini_stock.main.maybe_send_opening_silence_self_check",
        lambda settings, db, primary_symbols, now=None: helper_calls.append("self_check") or False,
    )

    settings = Settings(
        database_path=tmp_path / "signals.db",
        chart_dir=tmp_path,
        symbols=["TSLL"],
        benchmark_symbols=["SPY"],
        llm_provider="openai",
        data_provider="yfinance",
    )

    run_once(settings)

    span_names = [span.name for span in tracer.spans]
    assert "worker.run_once" in span_names
    assert "worker.run_movement_only" in span_names
    assert "worker.maintenance" in span_names
    root_span = next(span for span in tracer.spans if span.name == "worker.run_once")
    assert root_span.tags["data_provider"] == "yfinance"
    assert root_span.tags["llm_provider"] == "openai"
    assert root_span.tags["symbol_count"] == 1
    assert root_span.tags["benchmark_symbol_count"] == 1
    assert symbol_runs == ["primary:TSLL", "benchmark:SPY"]
    assert helper_calls == ["movement", "premarket", "maintenance", "self_check"]


def test_run_symbol_trace_sets_symbol_tags(monkeypatch, tmp_path):
    tracer = FakeTracer()
    monkeypatch.setattr("gemini_stock.main.tracer", tracer, raising=False)
    monkeypatch.setattr("gemini_stock.main.is_strong_candidate", lambda *args, **kwargs: False)
    candles_15m = _candles()
    candles_1m = _candles(interval_minutes=1)

    class FakeProvider:
        def get_ohlcv(self, symbol, interval, period):
            return candles_1m if interval == "1m" else candles_15m

    class FakeAnalyzer:
        def analyze_json_only(self, analysis_input):
            return _signal("TSLL", analysis_input.timestamp_utc, should_alert=False)

    context = RuntimeContext(
        data_provider=FakeProvider(),
        news_provider=SimpleNamespace(get_news=lambda symbols: []),
        renderer=SimpleNamespace(render_simplified=lambda *args, **kwargs: tmp_path / "chart.png"),
        analyzer=FakeAnalyzer(),
        fallback_analyzer=SimpleNamespace(analyze_json_only=lambda analysis_input: _signal("TSLL", analysis_input.timestamp_utc)),
        notifiers=[],
    )

    from gemini_stock.storage.db import Database

    db = Database(tmp_path / "signals.db")
    db.initialize()
    settings = Settings(database_path=db.path, chart_dir=tmp_path, feishu_webhook_url=None)

    result = run_symbol("TSLL", settings, db, AlertRuleEngine(), profile="primary", context=context)

    assert result is not None
    symbol_span = next(span for span in tracer.spans if span.name == "worker.run_symbol")
    assert symbol_span.tags["symbol"] == "TSLL"
    assert symbol_span.tags["profile"] == "primary"


def test_run_symbol_trace_propagates_failures(monkeypatch, tmp_path):
    tracer = FakeTracer()
    monkeypatch.setattr("gemini_stock.main.tracer", tracer, raising=False)

    class FailingProvider:
        def get_ohlcv(self, symbol, interval, period):
            raise RuntimeError("boom")

    context = RuntimeContext(
        data_provider=FailingProvider(),
        news_provider=SimpleNamespace(get_news=lambda symbols: []),
        renderer=SimpleNamespace(render_simplified=lambda *args, **kwargs: tmp_path / "chart.png"),
        analyzer=SimpleNamespace(analyze_json_only=lambda analysis_input: None),
        fallback_analyzer=SimpleNamespace(analyze_json_only=lambda analysis_input: None),
        notifiers=[],
    )

    from gemini_stock.storage.db import Database

    db = Database(tmp_path / "signals.db")
    db.initialize()
    settings = Settings(database_path=db.path, chart_dir=tmp_path, feishu_webhook_url=None)

    with pytest.raises(RuntimeError, match="boom"):
        run_symbol("TSLL", settings, db, AlertRuleEngine(), profile="primary", context=context)

    symbol_span = next(span for span in tracer.spans if span.name == "worker.run_symbol")
    assert symbol_span.error == "boom"


def test_docker_runtime_uses_ddtrace_run_and_distinct_service_names():
    root = Path(__file__).resolve().parent.parent
    compose = (root / "docker-compose.yml").read_text(encoding="utf-8")
    env_example = (root / ".env.example").read_text(encoding="utf-8")

    assert 'command: ["ddtrace-run", "python", "-m", "gemini_stock.web.app"]' in compose
    assert 'command: ["ddtrace-run", "python", "-m", "gemini_stock.main"]' in compose
    assert "DD_SERVICE=gemini-worker" in env_example
    assert "DD_DASHBOARD_SERVICE=gemini-dashboard" in env_example
    assert "DD_AGENT_HOST=121.196.154.93" in env_example
    assert "DD_AGENT_PORT=9529" in env_example


def test_tracing_env_configured_accepts_agent_port_alias():
    assert _tracing_env_configured(
        {
            "DD_AGENT_HOST": "",
            "DD_AGENT_PORT": "9529",
            "DD_TRACE_AGENT_PORT": "",
            "DD_SERVICE": "",
        }
    )
