from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

from gemini_stock.schemas import AlertDecision, GeminiSignal, TechnicalSnapshot


class AlertRuleEngine:
    def __init__(self, cooldown_minutes: int = 60) -> None:
        self.cooldown = timedelta(minutes=cooldown_minutes)
        self._last_alert_at: dict[str, datetime] = {}

    def evaluate(
        self,
        signal: GeminiSignal,
        technical_snapshot: TechnicalSnapshot,
        now: datetime | None = None,
    ) -> AlertDecision:
        current_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        symbol = signal.symbol

        if not signal.should_alert:
            return self._decision(False, "llm_should_alert is false", current_time, signal, technical_snapshot, severity="info")
        if signal.visual_confirmation == "rejected":
            return self._decision(False, "visual confirmation rejected", current_time, signal, technical_snapshot, severity="info")

        ema_50 = technical_snapshot.ema_50
        atr_14 = technical_snapshot.atr_14
        if ema_50 is not None and atr_14 is not None and technical_snapshot.close < ema_50 - 2 * atr_14:
            return self._decision(False, "price below EMA50 by more than 2 ATR", current_time, signal, technical_snapshot, severity="info")

        reason = "buy_alert" if _is_strong_buy_signal(signal, technical_snapshot) else "llm_alert"
        cooldown_key = self._cooldown_key(symbol, reason, signal)
        last_alert = self._last_alert_at.get(cooldown_key)
        if last_alert and current_time - last_alert < self.cooldown:
            return self._decision(False, "cooldown_active", current_time, signal, technical_snapshot, severity="info")
        self._last_alert_at[cooldown_key] = current_time
        return self._decision(True, reason, current_time, signal, technical_snapshot, severity=_primary_severity(reason, signal))

    @staticmethod
    def _cooldown_key(symbol: str, reason: str, signal: GeminiSignal) -> str:
        return ":".join([symbol, reason, signal.bias or "-", signal.setup_type or "-"])

    @staticmethod
    def _decision(
        should_alert: bool,
        reason: str,
        now: datetime,
        signal: GeminiSignal,
        technical_snapshot: TechnicalSnapshot,
        severity: str = "info",
    ) -> AlertDecision:
        return AlertDecision(
            symbol=signal.symbol,
            should_alert=should_alert,
            reason=reason,
            severity=severity,  # type: ignore[arg-type]
            timestamp_utc=now,
            signal=signal,
            technical_snapshot=technical_snapshot,
        )


class BenchmarkAlertRuleEngine:
    def __init__(self, cooldown_minutes: int = 60) -> None:
        self.cooldown = timedelta(minutes=cooldown_minutes)
        self._last_alert_at: dict[str, datetime] = {}

    def evaluate(
        self,
        signal: GeminiSignal,
        technical_snapshot: TechnicalSnapshot,
        candles_1m: pd.DataFrame,
        now: datetime | None = None,
    ) -> AlertDecision:
        current_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        symbol = signal.symbol

        reason = self._find_trigger_reason(signal, technical_snapshot, candles_1m)
        if not reason:
            return AlertRuleEngine._decision(False, "benchmark_no_alert", current_time, signal, technical_snapshot, severity="info")

        cooldown_key = self._cooldown_key(symbol, reason, signal)
        last_alert = self._last_alert_at.get(cooldown_key)
        if last_alert and current_time - last_alert < self.cooldown:
            return AlertRuleEngine._decision(False, "cooldown_active", current_time, signal, technical_snapshot, severity="info")
        self._last_alert_at[cooldown_key] = current_time
        return AlertRuleEngine._decision(True, reason, current_time, signal, technical_snapshot, severity=_benchmark_severity(reason))

    @staticmethod
    def _cooldown_key(symbol: str, reason: str, signal: GeminiSignal) -> str:
        return ":".join([symbol, reason, signal.bias or "-", signal.setup_type or "-"])

    def _find_trigger_reason(
        self,
        signal: GeminiSignal,
        technical_snapshot: TechnicalSnapshot,
        candles_1m: pd.DataFrame,
    ) -> str | None:
        if self._is_sudden_drop(candles_1m):
            return "benchmark_drop_alert"

        atr = technical_snapshot.atr_14 or 0
        close = technical_snapshot.close
        threshold = atr * 0.35
        nearest_support = _nearest_level(technical_snapshot.support_levels, close, prefer="support")
        nearest_resistance = _nearest_level(technical_snapshot.resistance_levels, close, prefer="resistance")

        if nearest_support is not None and abs(close - nearest_support) <= threshold:
            return "benchmark_support_watch"
        if nearest_resistance is not None and abs(nearest_resistance - close) <= threshold:
            return "benchmark_resistance_watch"

        if abs(signal.sentiment_score) >= 6 and signal.confidence >= 0.65:
            return "benchmark_trend_alert"
        return None

    @staticmethod
    def _is_sudden_drop(candles_1m: pd.DataFrame) -> bool:
        if candles_1m.empty or len(candles_1m) < 5:
            return False
        closes = candles_1m["close"].astype(float).tail(10)
        start_price = float(closes.iloc[0])
        end_price = float(closes.iloc[-1])
        if start_price <= 0:
            return False
        return (end_price - start_price) / start_price <= -0.012


def _nearest_level(levels: list[float], close: float, prefer: str) -> float | None:
    if not levels:
        return None
    if prefer == "support":
        candidates = [level for level in levels if level <= close]
        return max(candidates) if candidates else max(levels)
    candidates = [level for level in levels if level >= close]
    return min(candidates) if candidates else min(levels)


def _is_strong_buy_signal(signal: GeminiSignal, technical_snapshot: TechnicalSnapshot) -> bool:
    return (
        signal.sentiment_score > 7
        and signal.confidence > 0.65
        and (technical_snapshot.rsi_14 or 100) < 35
        and signal.risk_reward_ratio >= 1.5
        and signal.setup_type != "no_trade"
    )


def _primary_severity(reason: str, signal: GeminiSignal) -> str:
    if reason == "buy_alert":
        return "strong"
    if signal.setup_type == "no_trade" or abs(signal.sentiment_score) < 2:
        return "info"
    return "watch"


def _benchmark_severity(reason: str) -> str:
    if reason in {"benchmark_drop_alert", "benchmark_trend_alert"}:
        return "strong"
    return "watch"
