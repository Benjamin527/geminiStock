from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping, Sequence

import pandas as pd

from gemini_stock.schemas import TechnicalSnapshot

DEFAULT_MOVEMENT_ALERT_THRESHOLD_PCTS = [1.2, 2.0, 3.0]
DEFAULT_MOVEMENT_ALERT_WINDOW_MINUTES = 10


@dataclass(frozen=True)
class MovementAlert:
    symbol: str
    profile: str
    event_type: str
    tier: int
    repeat_count: int
    latest_price: float
    window_minutes: int
    drop_pct: float
    peak_price: float
    event_key: str
    timestamp_utc: datetime


def build_movement_alerts(
    symbol_or_snapshot: str | TechnicalSnapshot,
    candles_1m: pd.DataFrame,
    profile: str,
    trading_date: str,
    threshold_pcts: Sequence[float] | Mapping[str, Sequence[float]] | None = None,
    window_minutes: int = DEFAULT_MOVEMENT_ALERT_WINDOW_MINUTES,
) -> list[MovementAlert]:
    thresholds_by_event = _normalize_thresholds_by_event(threshold_pcts)
    if candles_1m.empty or len(candles_1m) < 2:
        return []

    recent = candles_1m.sort_values("timestamp").tail(max(window_minutes, 2))
    closes = recent["close"].astype(float)
    latest_price = float(closes.iloc[-1])
    peak_price = float(closes.max())
    trough_price = float(closes.min())
    if peak_price <= 0 or trough_price <= 0 or latest_price <= 0:
        return []

    drop_pct = (peak_price - latest_price) / peak_price * 100
    rise_pct = (latest_price - trough_price) / trough_price * 100
    event_type, move_pct, anchor_price = (
        ("fast_rise", rise_pct, trough_price)
        if rise_pct > drop_pct
        else ("fast_drop", drop_pct, peak_price)
    )
    tier = _matching_tier(move_pct, thresholds_by_event[event_type])
    if tier is None:
        return []

    symbol = _symbol_from(symbol_or_snapshot)
    timestamp = pd.Timestamp(recent.iloc[-1]["timestamp"]).to_pydatetime().astimezone(timezone.utc)
    event_key = ":".join(
        [
            "movement",
            symbol,
            event_type,
            f"tier{tier}",
            trading_date,
            _bucket(timestamp),
        ]
    )
    return [
        MovementAlert(
            symbol=symbol,
            profile=profile,
            event_type=event_type,
            tier=tier,
            repeat_count=tier,
            latest_price=latest_price,
            window_minutes=min(window_minutes, len(recent)),
            drop_pct=move_pct,
            peak_price=anchor_price,
            event_key=event_key,
            timestamp_utc=timestamp,
        )
    ]


def format_movement_alert(alert: MovementAlert) -> str:
    title = _movement_title(alert.profile)
    action = _movement_action(alert.profile, alert.event_type)
    tip = _movement_tip(alert.profile, with_prefix=True)
    move_label = _movement_move_label(alert.event_type)
    anchor_label = "低点" if alert.event_type == "fast_rise" else "高点"
    signed_pct = f"+{alert.drop_pct:.2f}%" if alert.event_type == "fast_rise" else f"-{alert.drop_pct:.2f}%"
    return "\n".join(
        [
            f"【{title}】{alert.symbol}｜{move_label} {alert.tier}档预警",
            "----------------",
            f"{alert.window_minutes}分钟 {signed_pct}｜现价 {alert.latest_price:.2f}｜{anchor_label} {alert.peak_price:.2f}",
            f"本档告警 {alert.repeat_count} 次｜第 {alert.tier} 档",
            f"动作：{action}",
            tip,
        ]
    )


def build_movement_alert_card(alert: MovementAlert) -> dict:
    title = _movement_title(alert.profile)
    action = _movement_action(alert.profile, alert.event_type)
    tip = _movement_tip(alert.profile, with_prefix=False)
    move_label = _movement_move_label(alert.event_type)
    anchor_label = "低点" if alert.event_type == "fast_rise" else "高点"
    signed_pct = f"+{alert.drop_pct:.2f}%" if alert.event_type == "fast_rise" else f"-{alert.drop_pct:.2f}%"
    template = "red" if alert.tier >= 3 else "orange" if alert.tier == 2 else "yellow"
    return {
        "config": {"wide_screen_mode": True, "enable_forward": True},
        "header": {
            "template": template,
            "title": {"tag": "plain_text", "content": f"{title} {alert.symbol}｜{move_label} {alert.tier}档"},
        },
        "elements": [
            {
                "tag": "markdown",
                "content": (
                    f"**周期**：{alert.window_minutes}分钟\n"
                    f"**{move_label}**：{signed_pct}\n"
                    f"**现价**：{alert.latest_price:.2f}\n"
                    f"**{anchor_label}**：{alert.peak_price:.2f}\n"
                    f"**本档告警**：{alert.repeat_count} 次｜第 {alert.tier} 档\n"
                    f"**动作**：{action}\n"
                    f"**提示**：{tip}"
                ),
            },
        ],
    }


def _normalize_thresholds(threshold_pcts: Sequence[float]) -> list[float]:
    thresholds = [float(value) for value in threshold_pcts]
    if len(thresholds) != 3:
        raise ValueError("movement alert thresholds must contain exactly 3 values")
    if any(value <= 0 for value in thresholds):
        raise ValueError("movement alert thresholds must be positive")
    if thresholds != sorted(thresholds) or len(set(thresholds)) != len(thresholds):
        raise ValueError("movement alert thresholds must be strictly ascending")
    return thresholds


def _normalize_thresholds_by_event(
    threshold_pcts: Sequence[float] | Mapping[str, Sequence[float]] | None,
) -> dict[str, list[float]]:
    if threshold_pcts is None:
        defaults = _normalize_thresholds(DEFAULT_MOVEMENT_ALERT_THRESHOLD_PCTS)
        return {"fast_drop": defaults, "fast_rise": defaults}
    if isinstance(threshold_pcts, Mapping):
        drop = _normalize_thresholds(threshold_pcts.get("fast_drop") or threshold_pcts.get("drop") or DEFAULT_MOVEMENT_ALERT_THRESHOLD_PCTS)
        rise = _normalize_thresholds(threshold_pcts.get("fast_rise") or threshold_pcts.get("rise") or drop)
        return {"fast_drop": drop, "fast_rise": rise}
    thresholds = _normalize_thresholds(threshold_pcts)
    return {"fast_drop": thresholds, "fast_rise": thresholds}


def _symbol_from(symbol_or_snapshot: str | TechnicalSnapshot) -> str:
    if isinstance(symbol_or_snapshot, str):
        return symbol_or_snapshot
    return symbol_or_snapshot.symbol


def _movement_title(profile: str) -> str:
    if profile == "benchmark":
        return "大盘异动"
    if profile == "crypto":
        return "币种异动"
    return "个股异动"


def _movement_action(profile: str, event_type: str) -> str:
    if event_type == "fast_rise":
        if profile == "benchmark":
            return "先看大盘持续性，不追高；已有盈利看分批止盈。"
        if profile == "crypto":
            return "先看加密市场持续性，避免追高或情绪加仓。"
        return "先看是否进入止盈/压力区，不追价；已有盈利可分批处理。"
    if profile == "benchmark":
        return "先看大盘承接，再判断个股信号。"
    if profile == "crypto":
        return "先看加密市场承接，避免追空或抢反弹。"
    return "先观察承接，不追空；等二次确认。"


def _movement_move_label(event_type: str) -> str:
    return "涨幅" if event_type == "fast_rise" else "跌幅"


def _movement_tip(profile: str, with_prefix: bool) -> str:
    if profile == "benchmark":
        text = "只看环境，不替代个股判断。"
    elif profile == "crypto":
        text = "仅价格异动提醒，不进入股票 AI 分析。"
    else:
        text = "仅研究提醒，注意仓位和大盘环境。"
    return f"提示：{text}" if with_prefix else text


def _matching_tier(drop_pct: float, thresholds: Sequence[float]) -> int | None:
    tier = None
    for index, threshold in enumerate(thresholds, start=1):
        if drop_pct >= threshold:
            tier = index
    return tier


def _bucket(timestamp: datetime) -> str:
    current = timestamp.astimezone(timezone.utc)
    bucket_minute = (current.minute // 30) * 30
    return f"{current.hour:02d}{bucket_minute:02d}"
