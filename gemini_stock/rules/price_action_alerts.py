from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd

from gemini_stock.schemas import GeminiSignal


@dataclass(frozen=True)
class PriceActionAlert:
    symbol: str
    action: str
    latest_price: float
    reference_label: str
    reference_zone: list[float]
    event_key: str
    timestamp_utc: datetime


def build_price_action_alerts(
    signal: GeminiSignal,
    candles_1m: pd.DataFrame,
    trading_date: str,
) -> list[PriceActionAlert]:
    if not signal.should_alert or signal.visual_confirmation == "rejected" or candles_1m.empty:
        return []

    latest = candles_1m.sort_values("timestamp").iloc[-1]
    latest_price = float(latest["close"])
    timestamp = pd.Timestamp(latest["timestamp"]).to_pydatetime().astimezone(timezone.utc)
    alerts: list[PriceActionAlert] = []

    entry_action = "trim" if signal.bias == "bearish" else "buy"
    entry_label = "AI 卖出参考" if signal.bias == "bearish" else "AI 买入参考"
    if _price_in_zone(latest_price, signal.entry_zone):
        alerts.append(_alert(signal, entry_action, latest_price, entry_label, signal.entry_zone, trading_date, timestamp))

    target_action = "cover" if signal.bias == "bearish" else "sell"
    target_label = "AI 回补买入" if signal.bias == "bearish" else "AI 卖出参考"
    if _price_in_zone(latest_price, signal.take_profit):
        alerts.append(_alert(signal, target_action, latest_price, target_label, signal.take_profit, trading_date, timestamp))

    return alerts


def format_price_action_alert(
    alert: PriceActionAlert,
    position_qty: float | None = None,
    average_cost: float | None = None,
) -> str:
    zone_title = {
        "buy": "买入区",
        "sell": "止盈区",
        "trim": "减仓区",
        "cover": "回补区",
    }.get(alert.action, "处理区")
    action_text = {
        "buy": "分批试探，别追价",
        "sell": "分批止盈，保留计划",
        "trim": "先减风险，不追空",
        "cover": "回补观察，别抢反弹",
    }.get(alert.action, "按计划处理")
    lines = [
        f"【价格到位】{alert.symbol}｜{zone_title}",
        "----------------",
        f"现价 {alert.latest_price:.2f}｜参考 {_format_zone(alert.reference_zone)}",
        f"动作：{action_text}",
    ]
    if position_qty is not None or average_cost is not None:
        position = f"{position_qty:g} 股" if position_qty is not None else "-"
        cost = f"{average_cost:.2f}" if average_cost is not None else "-"
        lines.append(f"持仓 {position}｜成本 {cost}")
    lines.append("提示：仅研究提醒，先看仓位和失效位。")
    return "\n".join(lines)


def _alert(
    signal: GeminiSignal,
    action: str,
    latest_price: float,
    reference_label: str,
    reference_zone: list[float],
    trading_date: str,
    timestamp: datetime,
) -> PriceActionAlert:
    normalized_zone = _normalize_zone(reference_zone)
    return PriceActionAlert(
        symbol=signal.symbol,
        action=action,
        latest_price=latest_price,
        reference_label=reference_label,
        reference_zone=normalized_zone,
        event_key=f"price_action:{signal.symbol}:{action}:{trading_date}:{_event_zone(normalized_zone)}",
        timestamp_utc=timestamp,
    )


def _price_in_zone(price: float, zone: list[float]) -> bool:
    if len(zone) < 2:
        return False
    low, high = _normalize_zone(zone)
    return low <= price <= high


def _normalize_zone(zone: list[float]) -> list[float]:
    ordered = sorted(float(value) for value in zone[:2])
    return [ordered[0], ordered[1]]


def _format_zone(zone: list[float]) -> str:
    low, high = _normalize_zone(zone)
    return f"{low:.2f}-{high:.2f}"


def _event_zone(zone: list[float]) -> str:
    low, high = _normalize_zone(zone)
    return f"{low:.2f}-{high:.2f}"
