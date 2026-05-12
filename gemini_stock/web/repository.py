from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yfinance as yf

from gemini_stock.benchmarks import build_benchmark_forecast
from gemini_stock.config import load_settings
from gemini_stock.health import evaluate_worker_health
from gemini_stock.schedule import get_schedule_decision
from gemini_stock.security import find_secret_config_warnings


BEIJING = ZoneInfo("Asia/Shanghai")


class DashboardRepository:
    def __init__(self, database_path: str | Path, chart_dir: str | Path) -> None:
        self.database_path = Path(database_path)
        self.chart_dir = Path(chart_dir)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        return conn

    def get_status(self) -> dict[str, Any]:
        decision = get_schedule_decision()
        settings = load_settings()
        latest_feature_time = self._latest_created_at("features")
        latest_llm_time = self._latest_created_at("llm_outputs")
        countdown_seconds = self._seconds_until_next_run(latest_llm_time, decision.interval_seconds, decision.should_run)
        worker_health = evaluate_worker_health(
            latest_llm_time,
            decision,
            stale_after_intervals=settings.worker_stale_after_intervals,
        )
        return {
            "market_session": decision.session.value,
            "should_run_now": decision.should_run,
            "interval_seconds": decision.interval_seconds,
            "countdown_seconds": countdown_seconds,
            "countdown_label": format_duration(countdown_seconds),
            "worker_health": worker_health,
            "config_warnings": find_secret_config_warnings(".env"),
            "latest_feature_at": to_beijing_time(latest_feature_time),
            "latest_llm_at": to_beijing_time(latest_llm_time),
            "database_path": str(self.database_path),
        }

    def get_today_metrics(self, symbols: list[str]) -> dict[str, Any]:
        feature_rows = self._rows_for_today("features", ["created_at_utc"])
        llm_rows = self._rows_for_today("llm_outputs", ["input_json", "output_json", "error", "created_at_utc"])
        alert_rows = self._rows_for_today("alerts", ["created_at_utc"])

        json_only = 0
        multimodal = 0
        image_calls = 0
        llm_errors = 0
        sentiment_values = []
        for row in llm_rows:
            payload = json.loads(row["input_json"])
            output = json.loads(row["output_json"]) if row["output_json"] else {}
            if payload.get("analysis_level") == "json_only":
                json_only += 1
            if payload.get("analysis_level") == "multimodal_review":
                multimodal += 1
            if payload.get("has_image"):
                image_calls += 1
            if row["error"]:
                llm_errors += 1
            if output.get("sentiment_score") is not None:
                sentiment_values.append(float(output["sentiment_score"]))

        symbol_count = max(len(symbols), 1)
        return {
            "symbol_scans_today": len(feature_rows),
            "estimated_run_cycles_today": math.ceil(len(feature_rows) / symbol_count) if feature_rows else 0,
            "json_only_calls_today": json_only,
            "multimodal_calls_today": multimodal,
            "image_calls_today": image_calls,
            "alerts_today": len(alert_rows),
            "llm_errors_today": llm_errors,
            "avg_sentiment_today": round(sum(sentiment_values) / len(sentiment_values), 2) if sentiment_values else None,
        }

    def get_symbol_states(self, symbols: list[str]) -> list[dict[str, Any]]:
        states = []
        for symbol in symbols:
            feature = self._latest_row("features", symbol)
            llm = self._latest_row("llm_outputs", symbol)
            latest_1m = self._latest_raw_candle(symbol, "1m")
            latest_15m = self._latest_raw_candle(symbol, "15m")
            feature_payload = self._json(feature, "payload_json") if feature else {}
            llm_input = self._json(llm, "input_json") if llm else {}
            llm_output = self._json(llm, "output_json") if llm and llm["output_json"] else {}
            chart = self._latest_chart(symbol)
            quote = _stored_quote_snapshot(feature_payload, latest_1m)
            states.append(
                {
                    "symbol": symbol,
                    "last_price": quote.get("regular_market_price") or feature_payload.get("close"),
                    "regular_market_price": quote.get("regular_market_price") or feature_payload.get("close"),
                    "post_market_price": quote.get("post_market_price"),
                    "price_source": quote.get("source") or ("feature_snapshot" if feature_payload else None),
                    "regular_market_time": to_beijing_time(quote.get("regular_market_time"))
                    or to_beijing_time(feature_payload.get("timestamp_utc")),
                    "post_market_time": to_beijing_time(quote.get("post_market_time")),
                    "rsi_14": feature_payload.get("rsi_14"),
                    "ema_50": feature_payload.get("ema_50"),
                    "vwap": feature_payload.get("vwap"),
                    "atr_14": feature_payload.get("atr_14"),
                    "feature_timestamp": to_beijing_time(feature_payload.get("timestamp_utc")),
                    "latest_1m_timestamp": to_beijing_time(latest_1m["timestamp_utc"]) if latest_1m else None,
                    "latest_15m_timestamp": to_beijing_time(latest_15m["timestamp_utc"]) if latest_15m else None,
                    "data_freshness": _freshness_state(latest_1m["timestamp_utc"] if latest_1m else None),
                    "latest_1m_price": latest_1m["close"] if latest_1m else None,
                    "latest_15m_price": latest_15m["close"] if latest_15m else None,
                    "analysis_level": llm_input.get("analysis_level") or llm_output.get("analysis_level"),
                    "has_image": llm_input.get("has_image"),
                    "sentiment_score": llm_output.get("sentiment_score"),
                    "confidence": llm_output.get("confidence"),
                    "bias": llm_output.get("bias"),
                    "setup_type": llm_output.get("setup_type"),
                    "visual_confirmation": llm_output.get("visual_confirmation"),
                    "should_alert": llm_output.get("should_alert"),
                    "entry_zone": llm_output.get("entry_zone"),
                    "stop_loss": llm_output.get("stop_loss"),
                    "take_profit": llm_output.get("take_profit"),
                    "risk_reward_ratio": llm_output.get("risk_reward_ratio"),
                    "technical_events": llm_input.get("technical_events", []),
                    "last_error": llm["error"] if llm else None,
                    "chart_path": chart,
                }
            )
        return states

    def get_benchmark_states(self, symbols: list[str]) -> list[dict[str, Any]]:
        states = []
        for symbol in symbols:
            feature = self._latest_row("features", symbol)
            llm = self._latest_row("llm_outputs", symbol)
            feature_payload = self._json(feature, "payload_json") if feature else {}
            llm_output = self._json(llm, "output_json") if llm and llm["output_json"] else {}
            latest_1m = self._latest_raw_candle(symbol, "1m")
            quote = _stored_quote_snapshot(feature_payload, latest_1m)
            close = quote.get("regular_market_price") or feature_payload.get("close")
            forecast = None
            if feature_payload and llm_output:
                try:
                    from gemini_stock.schemas import GeminiSignal, TechnicalSnapshot

                    snapshot = TechnicalSnapshot.model_validate(feature_payload)
                    signal = GeminiSignal.model_validate(llm_output)
                    forecast = build_benchmark_forecast(snapshot, signal)
                except Exception:
                    forecast = None

            support_levels = feature_payload.get("support_levels") or []
            resistance_levels = feature_payload.get("resistance_levels") or []
            nearest_support = forecast.support_level if forecast else _nearest_level_for_view(support_levels, close, "support")
            nearest_resistance = (
                forecast.resistance_level if forecast else _nearest_level_for_view(resistance_levels, close, "resistance")
            )
            states.append(
                {
                    "symbol": symbol,
                    "regular_market_price": close,
                    "regular_market_time": to_beijing_time(quote.get("regular_market_time"))
                    or to_beijing_time(feature_payload.get("timestamp_utc")),
                    "bias": llm_output.get("bias"),
                    "sentiment_score": llm_output.get("sentiment_score"),
                    "confidence": llm_output.get("confidence"),
                    "support_level": nearest_support,
                    "resistance_level": nearest_resistance,
                    "day_range": _format_price_range(forecast.day_range_low, forecast.day_range_high)
                    if forecast and forecast.day_range_low is not None and forecast.day_range_high is not None
                    else "-",
                    "rsi_14": feature_payload.get("rsi_14"),
                    "ema_20": feature_payload.get("ema_20"),
                    "ema_50": feature_payload.get("ema_50"),
                    "expected_move": forecast.expected_move if forecast else _expected_move(llm_output.get("bias"), close, nearest_support, nearest_resistance),
                    "outlook_note": forecast.outlook_note
                    if forecast
                    else _outlook_note(
                        llm_output.get("bias"),
                        close,
                        nearest_support,
                        nearest_resistance,
                        feature_payload.get("rsi_14"),
                    ),
                    "upside_scenario": forecast.upside_scenario if forecast else None,
                    "downside_scenario": forecast.downside_scenario if forecast else None,
                    "rebound_scenario": forecast.rebound_scenario if forecast else None,
                }
            )
        return states

    def get_recent_llm_outputs(self, symbols: list[str], limit: int = 20) -> list[dict[str, Any]]:
        if not self.database_path.exists():
            return []
        placeholders = ",".join("?" for _ in symbols)
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT symbol, input_json, output_json, error, created_at_utc
                FROM llm_outputs
                WHERE symbol IN ({placeholders})
                ORDER BY id DESC
                LIMIT ?
                """.format(placeholders=placeholders),
                (*symbols, limit),
            ).fetchall()
        results = []
        for row in rows:
            input_payload = json.loads(row["input_json"])
            output_payload = json.loads(row["output_json"]) if row["output_json"] else {}
            results.append(
                {
                    "symbol": row["symbol"],
                    "created_at_utc": to_beijing_time(row["created_at_utc"]),
                    "analysis_level": input_payload.get("analysis_level"),
                    "has_image": input_payload.get("has_image"),
                    "sentiment_score": output_payload.get("sentiment_score"),
                    "confidence": output_payload.get("confidence"),
                    "bias": output_payload.get("bias"),
                    "setup_type": output_payload.get("setup_type"),
                    "visual_confirmation": output_payload.get("visual_confirmation"),
                    "error": row["error"],
                }
            )
        return results

    def get_recent_alerts(self, symbols: list[str], limit: int = 20) -> list[dict[str, Any]]:
        if not self.database_path.exists():
            return []
        placeholders = ",".join("?" for _ in symbols)
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT symbol, channel, payload_json, created_at_utc
                FROM alerts
                WHERE symbol IN ({placeholders})
                ORDER BY id DESC
                LIMIT ?
                """.format(placeholders=placeholders),
                (*symbols, limit),
            ).fetchall()
        alerts = []
        for row in rows:
            payload = json.loads(row["payload_json"])
            signal = payload.get("signal", {})
            alert_type = payload.get("type")
            if alert_type == "movement_alert":
                drop_pct = payload.get("drop_pct")
                event_type = payload.get("event_type")
                sign = "+" if event_type == "fast_rise" else "-"
                sentiment_score = f"{sign}{float(drop_pct):.2f}%" if drop_pct is not None else None
                bias = f"{payload.get('tier')}档" if payload.get("tier") is not None else None
                setup_type = event_type or "movement_alert"
                confidence = payload.get("repeat_count")
            else:
                sentiment_score = signal.get("sentiment_score")
                bias = signal.get("bias")
                setup_type = signal.get("setup_type")
                confidence = signal.get("confidence")
            category = _alert_category(alert_type, setup_type)
            alerts.append(
                {
                    "symbol": row["symbol"],
                    "channel": row["channel"],
                    "created_at_utc": to_beijing_time(row["created_at_utc"]),
                    "category": category,
                    "bias": bias,
                    "sentiment_score": sentiment_score,
                    "confidence": confidence,
                    "setup_type": setup_type,
                }
            )
        return alerts

    def get_recent_errors(self, symbols: list[str], limit: int = 6) -> list[dict[str, Any]]:
        if not self.database_path.exists():
            return []
        placeholders = ",".join("?" for _ in symbols)
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT symbol, input_json, error, created_at_utc
                FROM llm_outputs
                WHERE symbol IN ({placeholders}) AND error IS NOT NULL AND error != ''
                ORDER BY id DESC
                LIMIT ?
                """.format(placeholders=placeholders),
                (*symbols, limit),
            ).fetchall()
        return [
            {
                "symbol": row["symbol"],
                "created_at_utc": to_beijing_time(row["created_at_utc"]),
                "analysis_level": json.loads(row["input_json"]).get("analysis_level"),
                "error": row["error"],
            }
            for row in rows
        ]

    def _latest_row(self, table: str, symbol: str) -> sqlite3.Row | None:
        if not self.database_path.exists():
            return None
        if table not in {"features", "llm_outputs"}:
            raise ValueError(f"unsupported table: {table}")
        with self.connect() as conn:
            return conn.execute(
                f"SELECT * FROM {table} WHERE symbol = ? ORDER BY id DESC LIMIT 1",
                (symbol,),
            ).fetchone()

    def _latest_raw_candle(self, symbol: str, interval: str) -> sqlite3.Row | None:
        if not self.database_path.exists():
            return None
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT timestamp_utc, close, created_at_utc
                FROM raw_candles
                WHERE symbol = ? AND interval = ?
                ORDER BY timestamp_utc DESC
                LIMIT 1
                """,
                (symbol, interval),
            ).fetchone()

    def _latest_created_at(self, table: str) -> str | None:
        if not self.database_path.exists():
            return None
        if table not in {"features", "llm_outputs", "alerts"}:
            raise ValueError(f"unsupported table: {table}")
        with self.connect() as conn:
            row = conn.execute(f"SELECT created_at_utc FROM {table} ORDER BY id DESC LIMIT 1").fetchone()
        return row["created_at_utc"] if row else None

    def _rows_for_today(self, table: str, columns: list[str]) -> list[sqlite3.Row]:
        if not self.database_path.exists():
            return []
        if table not in {"features", "llm_outputs", "alerts"}:
            raise ValueError(f"unsupported table: {table}")
        selected = ", ".join(columns)
        with self.connect() as conn:
            rows = conn.execute(f"SELECT {selected} FROM {table}").fetchall()
        today = datetime.now(BEIJING).date()
        return [row for row in rows if _parse_datetime(row["created_at_utc"]).astimezone(BEIJING).date() == today]

    @staticmethod
    def _seconds_until_next_run(latest_created_at: str | None, interval_seconds: int, should_run: bool) -> int:
        if not should_run:
            return interval_seconds
        if not latest_created_at:
            return 0
        next_run = _parse_datetime(latest_created_at) + _seconds_to_delta(interval_seconds)
        remaining = int((next_run - datetime.now(timezone.utc)).total_seconds())
        return max(remaining, 0)

    def _latest_chart(self, symbol: str) -> str | None:
        charts = sorted(self.chart_dir.glob(f"{symbol}_*.png"), key=lambda path: path.stat().st_mtime, reverse=True)
        if not charts:
            return None
        return f"/charts/{charts[0].name}"

    @staticmethod
    def _json(row: sqlite3.Row, column: str) -> dict[str, Any]:
        value = row[column]
        return json.loads(value) if value else {}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def to_beijing_time(value: str | None) -> str | None:
    if not value:
        return None
    return _parse_datetime(value).astimezone(BEIJING).strftime("%Y-%m-%d %H:%M:%S 北京时间")


def _parse_datetime(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _seconds_to_delta(seconds: int):
    from datetime import timedelta

    return timedelta(seconds=seconds)


def format_duration(seconds: int) -> str:
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}小时{minutes}分"
    if minutes:
        return f"{minutes}分{secs}秒"
    return f"{secs}秒"


def _freshness_state(timestamp_utc: str | None) -> dict[str, Any]:
    if not timestamp_utc:
        return {"state": "no_data", "age_seconds": None}
    age_seconds = max(0, int((datetime.now(timezone.utc) - _parse_datetime(timestamp_utc)).total_seconds()))
    if age_seconds <= 30 * 60:
        state = "fresh"
    elif age_seconds <= 6 * 60 * 60:
        state = "delayed"
    else:
        state = "stale"
    return {"state": state, "age_seconds": age_seconds}


def _fetch_quote_snapshot(symbol: str) -> dict[str, Any]:
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info or {}
        fast_info = ticker.fast_info or {}
    except Exception:
        return {}

    regular_market_price = info.get("regularMarketPrice") or fast_info.get("lastPrice")
    post_market_price = info.get("postMarketPrice")

    return {
        "regular_market_price": _safe_float(regular_market_price),
        "post_market_price": _safe_float(post_market_price),
        "regular_market_time": _timestamp_from_epoch(info.get("regularMarketTime")),
        "post_market_time": _timestamp_from_epoch(info.get("postMarketTime")),
        "source": "yfinance_quote",
    }


def _stored_quote_snapshot(feature_payload: dict[str, Any], latest_1m: sqlite3.Row | dict[str, Any] | None) -> dict[str, Any]:
    if latest_1m is not None:
        return {
            "regular_market_price": _safe_float(latest_1m["close"]),
            "post_market_price": None,
            "regular_market_time": latest_1m["timestamp_utc"],
            "post_market_time": None,
            "source": "stored_1m_candle",
        }
    if feature_payload:
        return {
            "regular_market_price": _safe_float(feature_payload.get("close")),
            "post_market_price": None,
            "regular_market_time": feature_payload.get("timestamp_utc"),
            "post_market_time": None,
            "source": "feature_snapshot",
        }
    return {}


def _timestamp_from_epoch(value: Any) -> str | None:
    if value in (None, ""):
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError, OSError):
        return None


def _safe_float(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _nearest_level_for_view(levels: list[float], close: float | None, prefer: str) -> float | None:
    if close is None or not levels:
        return levels[0] if levels else None
    if prefer == "support":
        candidates = [level for level in levels if level <= close]
        return max(candidates) if candidates else max(levels)
    candidates = [level for level in levels if level >= close]
    return min(candidates) if candidates else min(levels)


def _format_price_range(low: float | None, high: float | None) -> str:
    if low is None or high is None:
        return "-"
    return f"{low:.2f} - {high:.2f}"


def _alert_category(alert_type: str | None, setup_type: str | None) -> str:
    if alert_type in {"price_action", "price_action_p1", "price_action_p2"}:
        return "到价提醒"
    if alert_type == "movement_alert" or setup_type in {"fast_drop", "fast_rise", "movement_alert"}:
        return "价格异动"
    if alert_type == "benchmark_alert":
        return "大盘观察"
    if alert_type == "primary_alert":
        return "AI 盯盘"
    return "系统记录"


def _expected_move(bias: str | None, close: float | None, support: float | None, resistance: float | None) -> str:
    if close is None:
        return "等待数据"
    if bias == "bullish":
        if resistance is not None and resistance - close <= max(close * 0.003, 0.15):
            return "上探压力位"
        return "震荡偏强"
    if bias == "bearish":
        if support is not None and close - support <= max(close * 0.003, 0.15):
            return "下探支撑位"
        return "震荡偏弱"
    return "区间震荡"


def _outlook_note(
    bias: str | None,
    close: float | None,
    support: float | None,
    resistance: float | None,
    rsi_14: float | None,
) -> str:
    if close is None:
        return "等待最新行情。"
    if bias == "bullish":
        return (
            f"价格靠近压力位 {resistance:.2f}，短线留意突破延续。"
            if resistance is not None and resistance - close <= max(close * 0.003, 0.15)
            else "均线结构仍偏强，回踩后更适合观察承接。"
        )
    if bias == "bearish":
        return (
            f"价格靠近支撑位 {support:.2f}，留意是否继续下探。"
            if support is not None and close - support <= max(close * 0.003, 0.15)
            else "短线动能偏弱，反弹更适合观察压力确认。"
        )
    if rsi_14 is not None and 45 <= rsi_14 <= 55:
        return "方向不强，当前更像区间拉扯。"
    return "先看支撑压力的突破方向。"
