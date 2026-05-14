from datetime import datetime, timezone

import pandas as pd

from gemini_stock.rules.movement_alerts import build_movement_alert_card, build_movement_alerts, format_movement_alert
from gemini_stock.schemas import TechnicalSnapshot


def _technical(symbol: str = "TSLL", close: float = 12.18, rsi: float | None = 31.2) -> TechnicalSnapshot:
    return TechnicalSnapshot(
        symbol=symbol,
        interval="15m",
        timestamp_utc=datetime(2026, 1, 5, 15, 10, tzinfo=timezone.utc),
        open=12.8,
        high=12.9,
        low=12.1,
        close=close,
        volume=20000,
        rsi_14=rsi,
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


def _candles(prices: list[float], volumes: list[int] | None = None) -> pd.DataFrame:
    start = datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc)
    volumes = volumes or [400 for _ in prices[:-3]] + [2200, 2400, 2600]
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


def test_fast_drop_near_support_creates_primary_movement_alert():
    alerts = build_movement_alerts(
        _technical(),
        _candles([12.42, 12.38, 12.34, 12.31, 12.28, 12.24, 12.21, 12.19, 12.18, 12.18]),
        profile="primary",
        trading_date="2026-01-05",
        threshold_pcts=[1.2, 2.0, 3.0],
    )

    assert len(alerts) == 1
    alert = alerts[0]
    assert alert.symbol == "TSLL"
    assert alert.profile == "primary"
    assert alert.event_type == "fast_drop"
    assert alert.tier == 1
    assert alert.repeat_count == 1
    assert alert.drop_pct > 1.2
    assert alert.event_key == "movement:TSLL:fast_drop:tier1:2026-01-05:1500"
    text = format_movement_alert(alert)
    assert text.splitlines()[0] == "【个股异动】TSLL｜跌幅 1档预警"
    assert "10分钟" in text
    assert "本档告警 1 次" in text
    card = build_movement_alert_card(alert)
    assert card["header"]["title"]["content"] == "个股异动 TSLL｜跌幅 1档"
    assert "现价" in card["elements"][0]["content"]
    assert "本档告警" in card["elements"][0]["content"]


def test_fast_drop_for_crypto_uses_crypto_label():
    alerts = build_movement_alerts(
        "BTC-USD",
        _candles([100.0, 99.6, 99.1, 98.4, 97.8, 97.1, 96.6]),
        profile="crypto",
        trading_date="2026-01-05",
        threshold_pcts=[3.0, 6.0, 10.0],
    )

    assert len(alerts) == 1
    card = build_movement_alert_card(alerts[0])
    assert card["header"]["title"]["content"] == "币种异动 BTC-USD｜跌幅 1档"
    assert "先看加密市场承接" in card["elements"][0]["content"]


def test_deeper_drop_uses_highest_matching_tier_and_repeat_count():
    alerts = build_movement_alerts(
        _technical(close=11.98),
        _candles([12.42, 12.30, 12.22, 12.12, 12.06, 12.02, 11.99, 11.98, 11.98, 11.98]),
        profile="primary",
        trading_date="2026-01-05",
        threshold_pcts=[1.2, 2.0, 3.0],
    )

    assert len(alerts) == 1
    assert alerts[0].tier == 3
    assert alerts[0].repeat_count == 3
    assert alerts[0].event_key == "movement:TSLL:fast_drop:tier3:2026-01-05:1500"


def test_fast_rise_creates_take_profit_risk_alert():
    alerts = build_movement_alerts(
        _technical(close=12.95),
        _candles([12.42, 12.50, 12.58, 12.63, 12.70, 12.78, 12.84, 12.90, 12.94, 12.95]),
        profile="primary",
        trading_date="2026-01-05",
        threshold_pcts={"fast_drop": [3.0, 6.0, 10.0], "fast_rise": [1.2, 2.0, 3.0]},
    )

    assert len(alerts) == 1
    assert alerts[0].event_type == "fast_rise"
    assert alerts[0].tier == 3
    assert alerts[0].event_key == "movement:TSLL:fast_rise:tier3:2026-01-05:1500"
    text = format_movement_alert(alerts[0])
    assert text.splitlines()[0] == "【个股异动】TSLL｜涨幅 3档预警"
    assert "不追价" in text
    card = build_movement_alert_card(alerts[0])
    assert card["header"]["title"]["content"] == "个股异动 TSLL｜涨幅 3档"


def test_fast_rise_uses_rise_thresholds_when_split_from_drop_thresholds():
    alerts = build_movement_alerts(
        _technical(close=12.95),
        _candles([12.42, 12.50, 12.58, 12.63, 12.70, 12.78, 12.84, 12.90, 12.94, 12.95]),
        profile="primary",
        trading_date="2026-01-05",
        threshold_pcts={"fast_drop": [1.2, 2.0, 3.0], "fast_rise": [5.0, 8.0, 12.0]},
    )

    assert alerts == []


def test_small_move_does_not_create_alert():
    alerts = build_movement_alerts(
        _technical(),
        _candles([12.42, 12.41, 12.40, 12.39, 12.38, 12.37, 12.36, 12.35, 12.34, 12.33]),
        profile="primary",
        trading_date="2026-01-05",
        threshold_pcts=[1.2, 2.0, 3.0],
    )

    assert alerts == []


def test_fast_drop_without_volume_confirmation_does_not_alert():
    alerts = build_movement_alerts(
        "TSLL",
        _candles(
            [12.42, 12.38, 12.34, 12.31, 12.28, 12.24, 12.21, 12.19, 12.18, 12.18],
            volumes=[1000, 1000, 1000, 1000, 1000, 1000, 1000, 1000, 1000, 1000],
        ),
        profile="primary",
        trading_date="2026-01-05",
        threshold_pcts=[1.2, 2.0, 3.0],
    )

    assert alerts == []


def test_fast_drop_with_volume_confirmation_can_alert_without_snapshot_levels():
    alerts = build_movement_alerts(
        "TSLL",
        _candles(
            [12.42, 12.38, 12.34, 12.31, 12.28, 12.24, 12.21, 12.19, 12.18, 12.18],
            volumes=[400, 420, 450, 460, 500, 520, 2500, 2600, 2700, 2800],
        ),
        profile="primary",
        trading_date="2026-01-05",
        threshold_pcts=[1.2, 2.0, 3.0],
    )

    assert len(alerts) == 1
    assert alerts[0].event_type == "fast_drop"
