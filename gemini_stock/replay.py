from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from statistics import mean

from gemini_stock.schemas import Candle, GeminiSignal
from gemini_stock.storage.db import Database


@dataclass(frozen=True)
class SymbolReview:
    symbol: str
    bias: str
    setup_type: str
    first_price: float
    last_price: float
    high: float
    low: float
    entry_zone: list[float]
    stop_loss: float
    take_profit: list[float]
    entered: bool
    stopped: bool
    target_hit: bool
    outcome: str
    score: float
    lesson: str
    decision_level: str
    invalidation: str
    no_chase_zone: str
    position_constraint: str


@dataclass(frozen=True)
class DailyReview:
    trading_date: str
    evaluated_count: int
    hit_count: int
    accuracy_pct: float | None
    symbol_reviews: list[SymbolReview]
    learning_notes: list[str]


def summarize_alert_outcomes(
    db: Database,
    symbols: list[str],
    horizon_minutes: int = 120,
) -> dict:
    outcomes: list[float] = []
    by_setup_type: dict[str, list[float]] = {}
    placeholders = ",".join("?" for _ in symbols)
    if not placeholders:
        return _summary([])
    with db.connect() as conn:
        rows = conn.execute(
            f"""
            SELECT symbol, payload_json, created_at_utc
            FROM alerts
            WHERE symbol IN ({placeholders})
            ORDER BY id DESC
            LIMIT 200
            """,
            tuple(symbols),
        ).fetchall()
        for row in rows:
            try:
                payload = json.loads(row["payload_json"])
            except Exception:
                continue
            if payload.get("type") not in {"primary_alert", None}:
                continue
            setup_type = (payload.get("signal") or {}).get("setup_type") or "unknown"
            entry_price = (payload.get("technical_snapshot") or {}).get("close")
            if entry_price is None:
                continue
            created_at = _parse_datetime(row["created_at_utc"])
            horizon = created_at + timedelta(minutes=horizon_minutes)
            candles = conn.execute(
                """
                SELECT close
                FROM raw_candles
                WHERE symbol = ? AND interval = '15m' AND timestamp_utc > ? AND timestamp_utc <= ?
                ORDER BY timestamp_utc ASC
                """,
                (row["symbol"], created_at.isoformat(), horizon.isoformat()),
            ).fetchall()
            if not candles:
                continue
            forward_close = float(candles[-1]["close"])
            outcome = (forward_close - float(entry_price)) / float(entry_price) * 100
            outcomes.append(outcome)
            by_setup_type.setdefault(setup_type, []).append(outcome)
    summary = _summary(outcomes)
    summary["by_setup_type"] = {
        setup_type: _summary(values)
        for setup_type, values in sorted(by_setup_type.items())
    }
    return summary


def _summary(outcomes: list[float]) -> dict[str, float | int | None]:
    if not outcomes:
        return {
            "evaluated_alerts": 0,
            "avg_forward_return_pct": None,
            "hit_positive_pct": None,
        }
    return {
        "evaluated_alerts": len(outcomes),
        "avg_forward_return_pct": round(mean(outcomes), 2),
        "hit_positive_pct": round(sum(1 for value in outcomes if value > 0) / len(outcomes) * 100, 2),
    }


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def build_daily_review(db: Database, symbols: list[str], trading_date: date) -> DailyReview:
    signals = db.get_successful_signals_for_trading_date(symbols, trading_date)
    reviews: list[SymbolReview] = []
    for symbol in symbols:
        symbol_signals = [signal for signal in signals if signal.symbol == symbol]
        if not symbol_signals:
            continue
        signal = _prefer_signal(symbol_signals)
        candles = db.get_candles_for_trading_date(symbol, "1m", trading_date)
        if not candles:
            candles = db.get_candles_for_trading_date(symbol, "15m", trading_date)
        if not candles:
            continue
        reviews.append(_review_symbol(signal, candles))
    hit_count = sum(1 for review in reviews if review.outcome == "hit")
    accuracy = round(hit_count / len(reviews) * 100, 1) if reviews else None
    return DailyReview(
        trading_date=trading_date.isoformat(),
        evaluated_count=len(reviews),
        hit_count=hit_count,
        accuracy_pct=accuracy,
        symbol_reviews=reviews,
        learning_notes=_build_learning_notes(reviews),
    )


def format_daily_review(review: DailyReview) -> str:
    accuracy = "-" if review.accuracy_pct is None else f"{review.accuracy_pct:.1f}%"
    lines = [
        f"【每日复盘】{review.trading_date}",
        "----------------",
        f"评分：{review.hit_count}/{review.evaluated_count}｜准确率：{accuracy}",
    ]
    if review.symbol_reviews:
        for item in review.symbol_reviews:
            outcome_label = {"hit": "命中", "miss": "偏差", "neutral": "观望"}.get(item.outcome, item.outcome)
            lines.append(
                f"{item.symbol}｜{outcome_label}｜{item.decision_level}｜{item.first_price:.2f}->{item.last_price:.2f}｜高低 {item.high:.2f}/{item.low:.2f}"
            )
            lines.append(
                f"  失效条件：{_shorten(item.invalidation, 48)}｜不追价区：{_shorten(item.no_chase_zone, 42)}｜仓位：{_shorten(item.position_constraint, 32)}"
            )
    else:
        lines.append("无足够样本评分。")
        lines.append("结论：观察｜先记录 L1，等待二次握手，不为交易而交易。")
    if review.learning_notes:
        lines.append(f"学习：{_shorten(review.learning_notes[0], 34)}")
    return "\n".join(lines)


def _prefer_signal(signals: list[GeminiSignal]) -> GeminiSignal:
    return sorted(
        signals,
        key=lambda signal: (signal.timestamp_utc, 1 if signal.analysis_level == "multimodal_review" else 0),
    )[-1]


def _review_symbol(signal: GeminiSignal, candles: list[Candle]) -> SymbolReview:
    ordered = sorted(candles, key=lambda candle: candle.timestamp_utc)
    first = ordered[0]
    last = ordered[-1]
    high = max(candle.high for candle in ordered)
    low = min(candle.low for candle in ordered)
    entry_low, entry_high = _zone(signal.entry_zone)
    target_low, target_high = _zone(signal.take_profit)
    if signal.bias == "bearish":
        entered = low <= entry_high and high >= entry_low
        stopped = high >= signal.stop_loss
        target_hit = low <= target_low
    elif signal.bias == "bullish":
        entered = low <= entry_high and high >= entry_low
        stopped = low <= signal.stop_loss
        target_hit = high >= target_low
    else:
        entered = low <= entry_high and high >= entry_low
        stopped = False
        target_hit = False

    outcome = _classify_outcome(signal, entered, stopped, target_hit)
    lesson = _symbol_lesson(signal, entered, stopped, target_hit, first.close, last.close)
    decision_level = _decision_level(signal, entered, stopped, target_hit, outcome)
    invalidation = _invalidation_condition(signal, entry_low, entry_high)
    no_chase_zone = _no_chase_zone(signal, entry_low, entry_high)
    position_constraint = _position_constraint(signal, outcome)
    score = 1.0 if outcome == "hit" else 0.5 if outcome == "neutral" else 0.0
    return SymbolReview(
        symbol=signal.symbol,
        bias=signal.bias,
        setup_type=signal.setup_type,
        first_price=first.close,
        last_price=last.close,
        high=high,
        low=low,
        entry_zone=[entry_low, entry_high],
        stop_loss=signal.stop_loss,
        take_profit=[target_low, target_high],
        entered=entered,
        stopped=stopped,
        target_hit=target_hit,
        outcome=outcome,
        score=score,
        lesson=lesson,
        decision_level=decision_level,
        invalidation=invalidation,
        no_chase_zone=no_chase_zone,
        position_constraint=position_constraint,
    )


def _classify_outcome(signal: GeminiSignal, entered: bool, stopped: bool, target_hit: bool) -> str:
    if signal.bias == "neutral" or signal.setup_type == "no_trade":
        return "hit" if not entered else "neutral"
    if not entered:
        return "neutral"
    if target_hit and not stopped:
        return "hit"
    if stopped and not target_hit:
        return "miss"
    return "hit" if target_hit else "miss"


def _symbol_lesson(signal: GeminiSignal, entered: bool, stopped: bool, target_hit: bool, first_close: float, last_close: float) -> str:
    if signal.bias == "neutral" or signal.setup_type == "no_trade":
        return "观望信号优先检查是否避免追价和情绪交易。"
    if not entered:
        return "价格没有进入参考区，后续继续等待二次握手，不追离 L1 太远的反弹。"
    if target_hit and not stopped:
        return "建议与走势匹配，保留分批止盈和尾盘确认规则。"
    if stopped:
        return "信号失效条件被触发，后续要更重视 L1 跌破、板块同步和仓位约束。"
    move_pct = (last_close - first_close) / first_close * 100 if first_close else 0
    if signal.bias == "bullish" and move_pct < 0:
        return "偏多判断和收盘方向不一致，后续降低首次试探仓位，等待二次握手确认。"
    if signal.bias == "bearish" and move_pct > 0:
        return "偏空判断和收盘方向不一致，后续避免在回补买入区附近继续看空。"
    return "走势尚未验证目标，继续把不追价区、失效条件和仓位约束写清楚。"


def _build_learning_notes(reviews: list[SymbolReview]) -> list[str]:
    if not reviews:
        return ["没有足够样本，不更新交易偏好；下一次继续先记录 L1，再等二次握手。"]
    notes = ["每天固定复盘建议与实际走势，保留 L1、二次握手、失效条件和不追价区四个字段。"]
    misses = [review for review in reviews if review.outcome == "miss"]
    if misses:
        symbols = "、".join(review.symbol for review in misses)
        notes.append(f"{symbols} 出现偏差，后续先降首次试探仓位，并要求大盘/板块/尾盘至少两项确认。")
    else:
        notes.append("当日没有明确失败样本，但仍不能放松仓位约束，尤其是 CONL、TSLL 这类高波动标的。")
    neutral = [review for review in reviews if review.outcome == "neutral"]
    if neutral:
        notes.append("未触发参考区的信号不强行评分为失败，继续等待回踩或尾盘确认，避免为了交易而交易。")
    notes.append("次日盘中先看结论分级，再看价位区间与条件，最后才看仓位，避免情绪先行。")
    return notes


def _decision_level(signal: GeminiSignal, entered: bool, stopped: bool, target_hit: bool, outcome: str) -> str:
    if stopped or outcome == "miss":
        return "失效"
    if signal.setup_type == "no_trade" or signal.bias == "neutral":
        return "观察"
    if not entered:
        return "等二次握手"
    if target_hit:
        return "确认后小仓"
    return "试探"


def _invalidation_condition(signal: GeminiSignal, entry_low: float, entry_high: float) -> str:
    if signal.setup_type == "no_trade" or signal.bias == "neutral":
        return f"未出现二次握手前，不开新仓；若跌破 {entry_low:.2f} 附近弱支撑继续观望。"
    if signal.bias == "bearish":
        return f"有效站上风险位 {signal.stop_loss:.2f} 且未回落，视为失效。"
    return f"有效跌破止损位 {signal.stop_loss:.2f} 且未收回，视为失效。"


def _no_chase_zone(signal: GeminiSignal, entry_low: float, entry_high: float) -> str:
    if signal.bias == "bearish":
        return f"跌离卖出参考 {entry_low:.2f}-{entry_high:.2f} 后不追空，等反抽结构。"
    return f"高于买入参考上沿 {entry_high:.2f} 后快速拉升，不追价。"


def _position_constraint(signal: GeminiSignal, outcome: str) -> str:
    if signal.setup_type == "no_trade" or signal.bias == "neutral":
        return "0-0.5 成观察仓，仅做记录。"
    if outcome == "miss":
        return "下一轮降至轻仓试探，等待二次握手再加。"
    if signal.bias == "bearish":
        return "先减仓后观察，避免重仓单向押注。"
    return "先轻仓试探，确认后分批，不让单标的过重。"


def _zone(values: list[float]) -> tuple[float, float]:
    if len(values) < 2:
        value = float(values[0]) if values else 0.0
        return value, value
    low, high = sorted(float(value) for value in values[:2])
    return low, high


def _shorten(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + "…"
