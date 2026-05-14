from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd

from gemini_stock.config import Settings
from gemini_stock.main import maybe_send_movement_alerts, run_movement_only_symbol
from gemini_stock.schemas import TechnicalSnapshot
from gemini_stock.storage.db import Database


def _technical(symbol: str = "TSLL", close: float = 12.18) -> TechnicalSnapshot:
    return TechnicalSnapshot(
        symbol=symbol,
        interval="15m",
        timestamp_utc=datetime(2026, 1, 5, 15, 10, tzinfo=timezone.utc),
        open=12.8,
        high=12.9,
        low=12.1,
        close=close,
        volume=20000,
        rsi_14=31.2,
        macd=-0.02,
        macd_signal=0.01,
        macd_histogram=-0.03,
        ema_20=12.5,
        ema_50=12.7,
        vwap=12.4,
        atr_14=0.22,
        recent_4h_high=13.1,
        recent_4h_low=12.0,
        support_levels=[12.1, 11.8],
        resistance_levels=[12.9, 13.2],
    )


def _candles() -> pd.DataFrame:
    prices = [12.42, 12.38, 12.34, 12.31, 12.28, 12.24, 12.21, 12.19, 12.18, 12.18]
    start = datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc)
    volumes = [400, 420, 450, 460, 500, 520, 2500, 2600, 2700, 2800]
    return pd.DataFrame(
        [
            {
                "timestamp": start.replace(minute=idx),
                "open": price,
                "high": price + 0.03,
                "low": price - 0.03,
                "close": price,
                "volume": volumes[idx],
            }
            for idx, price in enumerate(prices)
        ]
    )


def test_maybe_send_movement_alerts_sends_and_deduplicates_feishu(tmp_path, monkeypatch):
    sent_cards = []
    monkeypatch.setattr("gemini_stock.main.send_feishu_interactive_card", lambda webhook_url, card, **kwargs: sent_cards.append(card) or True)
    monkeypatch.setattr("gemini_stock.main.send_feishu_text", lambda webhook_url, text, **kwargs: (_ for _ in ()).throw(AssertionError("movement alerts should use cards")))
    settings = Settings(feishu_webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/token")
    db = Database(tmp_path / "signals.db")
    db.initialize()
    db.save_movement_alert_thresholds([1.2, 2.0, 3.0])

    first = maybe_send_movement_alerts(settings, db, _technical(), _candles(), profile="primary", trading_date="2026-01-05")
    second = maybe_send_movement_alerts(settings, db, _technical(), _candles(), profile="primary", trading_date="2026-01-05")

    assert first == 1
    assert second == 0
    assert len(sent_cards) == 1
    assert sent_cards[0]["header"]["title"]["content"] == "个股异动 TSLL｜跌幅 1档"
    assert db.has_alert_event("feishu", "movement:TSLL:fast_drop:tier1:2026-01-05:1500:n1") is True


def test_maybe_send_movement_alerts_repeats_by_tier(tmp_path, monkeypatch):
    sent_cards = []
    monkeypatch.setattr("gemini_stock.main.send_feishu_interactive_card", lambda webhook_url, card, **kwargs: sent_cards.append(card) or True)
    monkeypatch.setattr("gemini_stock.main.send_feishu_text", lambda webhook_url, text, **kwargs: (_ for _ in ()).throw(AssertionError("movement alerts should use cards")))
    settings = Settings(feishu_webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/token")
    db = Database(tmp_path / "signals.db")
    db.initialize()
    db.save_movement_alert_thresholds([1.2, 2.0, 3.0])
    candles = _candles().assign(close=[12.42, 12.30, 12.22, 12.12, 12.06, 12.02, 11.99, 11.98, 11.98, 11.98])

    count = maybe_send_movement_alerts(settings, db, _technical(close=11.98), candles, profile="primary", trading_date="2026-01-05")

    assert count == 3
    assert len(sent_cards) == 3
    assert all(card["header"]["title"]["content"] == "个股异动 TSLL｜跌幅 3档" for card in sent_cards)
    assert db.has_alert_event("feishu", "movement:TSLL:fast_drop:tier3:2026-01-05:1500:n3") is True


def test_maybe_send_movement_alerts_sends_fast_rise_card(tmp_path, monkeypatch):
    sent_cards = []
    monkeypatch.setattr("gemini_stock.main.send_feishu_interactive_card", lambda webhook_url, card, **kwargs: sent_cards.append(card) or True)
    settings = Settings(feishu_webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/token")
    db = Database(tmp_path / "signals.db")
    db.initialize()
    db.save_movement_alert_thresholds([1.2, 2.0, 3.0])
    candles = _candles().assign(close=[12.42, 12.50, 12.58, 12.63, 12.70, 12.78, 12.84, 12.90, 12.94, 12.95])

    count = maybe_send_movement_alerts(settings, db, _technical(close=12.95), candles, profile="primary", trading_date="2026-01-05")

    assert count == 3
    assert sent_cards[0]["header"]["title"]["content"] == "个股异动 TSLL｜涨幅 3档"
    assert db.has_alert_event("feishu", "movement:TSLL:fast_rise:tier3:2026-01-05:1500:n3") is True


def test_run_movement_only_symbol_scans_btc_without_ai_or_15m(tmp_path, monkeypatch):
    sent_cards = []
    calls = []

    class FakeProvider:
        def get_ohlcv(self, symbol, interval, period):
            calls.append((symbol, interval, period))
            return _candles().assign(close=[100.0, 99.6, 99.1, 98.4, 97.8, 97.1, 96.6, 96.6, 96.6, 96.6])

    monkeypatch.setattr("gemini_stock.main.send_feishu_interactive_card", lambda webhook_url, card, **kwargs: sent_cards.append(card) or True)
    monkeypatch.setattr("gemini_stock.main.send_feishu_text", lambda webhook_url, text, **kwargs: (_ for _ in ()).throw(AssertionError("BTC movement alerts should use cards")))
    settings = Settings(
        feishu_webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/token",
        movement_alert_symbols=["BTC-USD"],
    )
    db = Database(tmp_path / "signals.db")
    db.initialize()
    db.save_movement_alert_thresholds([3.0, 6.0, 10.0])

    count = run_movement_only_symbol(
        "BTC-USD",
        settings,
        db,
        context=SimpleNamespace(data_provider=FakeProvider()),
        trading_date="2026-01-05",
    )

    assert count == 1
    assert calls == [("BTC-USD", "1m", settings.yfinance_period_1m)]
    assert sent_cards[0]["header"]["title"]["content"] == "币种异动 BTC-USD｜跌幅 1档"


def test_run_movement_only_symbol_uses_thirty_minute_window(tmp_path, monkeypatch):
    captured = {}

    class FakeProvider:
        def get_ohlcv(self, symbol, interval, period):
            return _candles()

    monkeypatch.setattr(
        "gemini_stock.main.maybe_send_movement_alerts",
        lambda settings, db, technical_snapshot, candles_1m, profile, trading_date=None, window_minutes=10: captured.update(
            {
                "profile": profile,
                "window_minutes": window_minutes,
                "symbol": technical_snapshot,
            }
        )
        or 0,
    )

    settings = Settings(movement_alert_symbols=["BTC-USD"])
    db = Database(tmp_path / "signals.db")
    db.initialize()

    run_movement_only_symbol(
        "BTC-USD",
        settings,
        db,
        context=SimpleNamespace(data_provider=FakeProvider()),
        trading_date="2026-01-05",
    )

    assert captured == {"profile": "crypto", "window_minutes": 30, "symbol": "BTC-USD"}
