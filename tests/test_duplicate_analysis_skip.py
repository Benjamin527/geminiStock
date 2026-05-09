from datetime import datetime, timedelta, timezone

import pandas as pd

from gemini_stock.config import Settings
from gemini_stock.features.snapshot import build_technical_snapshot
from gemini_stock.main import run_symbol
from gemini_stock.rules.alert_rules import AlertRuleEngine
from gemini_stock.schemas import GeminiSignal
from gemini_stock.storage.db import Database


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


def _signal(symbol: str, timestamp: datetime) -> GeminiSignal:
    return GeminiSignal(
        symbol=symbol,
        timestamp_utc=timestamp,
        analysis_level="json_only",
        bias="bullish",
        sentiment_score=6.4,
        confidence=0.7,
        setup_type="breakout",
        visual_confirmation="not_applicable",
        should_alert=True,
        entry_zone=[103.8, 104.0],
        stop_loss=102.0,
        take_profit=[105.0, 106.0],
        risk_reward_ratio=1.5,
        reasons=["Existing signal."],
        risk_warnings=["Fast moves can reverse."],
    )


def test_run_symbol_reuses_existing_signal_for_same_15m_snapshot(monkeypatch, tmp_path):
    candles_15m = _candles()
    candles_1m = _candles(interval_minutes=1)
    snapshot = build_technical_snapshot("TSLL", candles_15m)
    signal = _signal("TSLL", snapshot.timestamp_utc)
    db = Database(tmp_path / "signals.db")
    db.initialize()
    db.save_llm_output(
        "TSLL",
        {"technical_snapshot": {"timestamp_utc": snapshot.timestamp_utc.isoformat()}},
        signal.json_dict(),
        None,
    )

    class FakeProvider:
        def __init__(self, delayed_tolerance_minutes):
            pass

        def get_ohlcv(self, symbol, interval, period):
            return candles_1m if interval == "1m" else candles_15m

    class FakeAnalyzer:
        calls = 0

        def analyze_json_only(self, analysis_input):
            self.calls += 1
            return signal

    analyzer = FakeAnalyzer()
    monkeypatch.setattr("gemini_stock.main.YFinanceMarketDataProvider", FakeProvider)
    monkeypatch.setattr("gemini_stock.main.create_analyzer", lambda settings: analyzer)

    result = run_symbol(
        "TSLL",
        Settings(database_path=tmp_path / "signals.db", chart_dir=tmp_path, feishu_webhook_url=None),
        db,
        AlertRuleEngine(),
        profile="primary",
    )

    assert result is not None
    assert result.signal.symbol == "TSLL"
    assert analyzer.calls == 0
