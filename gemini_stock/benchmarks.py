from __future__ import annotations

from dataclasses import dataclass

from gemini_stock.schemas import GeminiSignal, TechnicalSnapshot


@dataclass(frozen=True)
class BenchmarkForecast:
    symbol: str
    bias: str
    current_price: float
    support_level: float | None
    resistance_level: float | None
    expected_move: str
    outlook_note: str
    upside_scenario: str
    downside_scenario: str
    rebound_scenario: str
    day_range_low: float | None = None
    day_range_high: float | None = None


def build_benchmark_forecast(snapshot: TechnicalSnapshot, signal: GeminiSignal) -> BenchmarkForecast:
    atr = snapshot.atr_14 or max(snapshot.close * 0.008, 1.0)
    support = _nearest_support(snapshot.support_levels, snapshot.close)
    resistance = _nearest_resistance(snapshot.resistance_levels, snapshot.close)
    day_range_low, day_range_high = _regular_session_day_range(snapshot, signal.bias)
    weak_trigger, strong_trigger = _regular_session_triggers(snapshot.close, atr, day_range_low, day_range_high, support, resistance)
    midpoint = (day_range_low + day_range_high) / 2

    expected_move = _expected_move(signal.bias, day_range_low, day_range_high)
    outlook_note = _outlook_note(signal.bias, weak_trigger, strong_trigger, snapshot.rsi_14)

    strong_text = _fmt_level(strong_trigger)
    weak_text = _fmt_level(weak_trigger)
    high_text = _fmt_level(day_range_high)
    low_text = _fmt_level(day_range_low)
    midpoint_text = _fmt_level(midpoint)
    upside_scenario = (
        f"走强路径：常规盘若上破 {strong_text}，今日更容易向 {midpoint_text}-{high_text} 扩展。"
    )
    downside_scenario = (
        f"走弱路径：常规盘若跌破 {weak_text}，今日更容易回看 {low_text}-{midpoint_text}。"
    )
    if signal.bias == "bearish":
        rebound_scenario = (
            f"反弹若站不回 {strong_text}，常规盘仍按偏弱处理；重新站上后再看区间上沿 {high_text}。"
        )
    elif signal.bias == "bullish":
        rebound_scenario = f"回踩只要守住 {weak_text}，常规盘主路径仍偏向 {midpoint_text}-{high_text}。"
    else:
        rebound_scenario = f"区间内先按震荡处理，等待 {weak_text} 或 {strong_text} 被有效突破。"

    return BenchmarkForecast(
        symbol=snapshot.symbol,
        bias=signal.bias,
        current_price=snapshot.close,
        support_level=weak_trigger,
        resistance_level=strong_trigger,
        expected_move=expected_move,
        outlook_note=outlook_note,
        upside_scenario=upside_scenario,
        downside_scenario=downside_scenario,
        rebound_scenario=rebound_scenario,
        day_range_low=day_range_low,
        day_range_high=day_range_high,
    )


def _nearest_support(levels: list[float], close: float) -> float | None:
    if not levels:
        return None
    candidates = [level for level in levels if level <= close]
    return max(candidates) if candidates else max(levels)


def _nearest_resistance(levels: list[float], close: float) -> float | None:
    if not levels:
        return None
    candidates = [level for level in levels if level >= close]
    return min(candidates) if candidates else min(levels)


def _regular_session_day_range(snapshot: TechnicalSnapshot, bias: str) -> tuple[float, float]:
    close = snapshot.close
    atr = snapshot.atr_14 or close * 0.004
    recent_span = max(snapshot.recent_4h_high - snapshot.recent_4h_low, 0)
    range_floor = close * 0.01
    range_cap = close * 0.025
    projected_range = min(max(range_floor, atr * 6, recent_span * 1.05), range_cap)
    if bias == "bullish":
        low = close - projected_range * 0.35
        high = close + projected_range * 0.65
    elif bias == "bearish":
        low = close - projected_range * 0.65
        high = close + projected_range * 0.35
    else:
        low = close - projected_range * 0.5
        high = close + projected_range * 0.5
    return round(low, 2), round(high, 2)


def _regular_session_triggers(
    close: float,
    atr: float,
    day_range_low: float,
    day_range_high: float,
    support: float | None,
    resistance: float | None,
) -> tuple[float, float]:
    day_range = day_range_high - day_range_low
    min_distance = max(day_range * 0.18, min(atr * 1.5, day_range * 0.35), close * 0.0025)
    weak_trigger = min(support if support is not None else close - min_distance, close - min_distance)
    strong_trigger = max(resistance if resistance is not None else close + min_distance, close + min_distance)
    return round(weak_trigger, 2), round(strong_trigger, 2)


def _expected_move(bias: str, day_range_low: float, day_range_high: float) -> str:
    range_text = f"{_fmt_level(day_range_low)}-{_fmt_level(day_range_high)}"
    if bias == "bullish":
        return f"常规盘偏强，预估区间 {range_text}"
    if bias == "bearish":
        return f"常规盘偏弱，预估区间 {range_text}"
    return f"常规盘震荡，预估区间 {range_text}"


def _outlook_note(
    bias: str,
    weak_trigger: float,
    strong_trigger: float,
    rsi_14: float | None,
) -> str:
    trigger_text = f"{_fmt_level(weak_trigger)}-{_fmt_level(strong_trigger)}"
    if bias == "bullish":
        return f"预测针对美股常规盘 09:30-16:00；结构偏强，重点看 {trigger_text} 的方向确认。"
    if bias == "bearish":
        return f"预测针对美股常规盘 09:30-16:00；结构偏弱，反弹先看 {trigger_text} 能否重新站回。"
    if rsi_14 is not None and 45 <= rsi_14 <= 55:
        return f"预测针对美股常规盘 09:30-16:00；方向不强，先看 {trigger_text} 的区间选择。"
    return f"预测针对美股常规盘 09:30-16:00；先看 {trigger_text} 的突破方向。"


def _fmt_level(value: float) -> str:
    return f"{value:.2f}"
