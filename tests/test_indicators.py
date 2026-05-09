from datetime import datetime, timedelta, timezone

import pandas as pd

from gemini_stock.features.snapshot import build_technical_snapshot


def _candles(rows: int = 40) -> pd.DataFrame:
    start = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
    data = []
    for i in range(rows):
        close = 100 + i * 0.5
        data.append(
            {
                "timestamp": start + timedelta(minutes=15 * i),
                "open": close - 0.2,
                "high": close + 0.5,
                "low": close - 0.5,
                "close": close,
                "volume": 1000 + i * 10,
            }
        )
    return pd.DataFrame(data)


def test_build_technical_snapshot_contains_required_indicators():
    snapshot = build_technical_snapshot("SPY", _candles())

    assert snapshot.symbol == "SPY"
    assert snapshot.interval == "15m"
    assert snapshot.rsi_14 is not None
    assert snapshot.macd is not None
    assert snapshot.macd_signal is not None
    assert snapshot.ema_20 is not None
    assert snapshot.ema_50 is not None
    assert snapshot.vwap is not None
    assert snapshot.atr_14 is not None
    assert len(snapshot.support_levels) >= 1
    assert len(snapshot.resistance_levels) >= 1


def test_recent_four_hour_high_low_uses_last_sixteen_15m_bars():
    df = _candles(40)
    df.loc[0, "high"] = 999
    df.loc[39, "high"] = 150
    df.loc[0, "low"] = 1
    df.loc[39, "low"] = 95

    snapshot = build_technical_snapshot("QQQ", df)

    assert snapshot.recent_4h_high == 150
    assert snapshot.recent_4h_low == 95
