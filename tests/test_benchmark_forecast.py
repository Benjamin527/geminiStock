from datetime import datetime, timezone

from gemini_stock.benchmarks import BenchmarkForecast, build_benchmark_forecast
from gemini_stock.schemas import GeminiSignal, TechnicalSnapshot


def _snapshot() -> TechnicalSnapshot:
    return TechnicalSnapshot(
        symbol="QQQ",
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


def _signal(bias: str = "bullish", score: float = 5.0) -> GeminiSignal:
    return GeminiSignal(
        symbol="QQQ",
        timestamp_utc=datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc),
        analysis_level="json_only",
        bias=bias,  # type: ignore[arg-type]
        sentiment_score=score,
        confidence=0.7,
        setup_type="breakout" if bias == "bullish" else "breakdown",
        visual_confirmation="not_applicable",
        should_alert=False,
        entry_zone=[647, 649],
        stop_loss=642,
        take_profit=[662, 670],
        risk_reward_ratio=1.8,
        reasons=["test"],
        risk_warnings=["test"],
    )


def test_build_benchmark_forecast_creates_regular_session_day_range():
    forecast = build_benchmark_forecast(_snapshot(), _signal())

    assert isinstance(forecast, BenchmarkForecast)
    assert forecast.symbol == "QQQ"
    assert forecast.day_range_low is not None
    assert forecast.day_range_high is not None
    assert forecast.day_range_high - forecast.day_range_low > 10
    assert forecast.expected_move.startswith("常规盘偏强")
    assert "09:30-16:00" in forecast.outlook_note
    assert "短线" not in forecast.upside_scenario
    assert "走强路径" in forecast.upside_scenario
    assert "走弱路径" in forecast.downside_scenario


def test_build_benchmark_forecast_handles_bearish_bias():
    forecast = build_benchmark_forecast(_snapshot(), _signal(bias="bearish", score=-6.0))

    assert forecast.bias == "bearish"
    assert forecast.day_range_low is not None
    assert forecast.day_range_high is not None
    assert forecast.current_price - forecast.day_range_low > forecast.day_range_high - forecast.current_price
    assert forecast.expected_move.startswith("常规盘偏弱")
    assert "走弱路径" in forecast.downside_scenario
    assert "反弹" in forecast.rebound_scenario
