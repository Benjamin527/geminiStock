from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pymysql
from pymysql.cursors import DictCursor

from gemini_stock.benchmarks import build_benchmark_forecast
from gemini_stock.config import Settings
from gemini_stock.schemas import GeminiSignal, TechnicalSnapshot
from gemini_stock.web.repository import (
    BEIJING,
    _expected_move,
    _format_price_range,
    _nearest_level_for_view,
    _outlook_note,
    _parse_datetime,
    _stored_quote_snapshot,
    format_duration,
    to_beijing_time,
)
from gemini_stock.schedule import get_schedule_decision


class MySQLDashboardRepository:
    def __init__(self, settings: Settings, chart_dir: str | Path) -> None:
        self.settings = settings
        self.chart_dir = Path(chart_dir)

    def connect(self):
        return pymysql.connect(
            host=self.settings.remote_mysql_host,
            port=self.settings.remote_mysql_port,
            user=self.settings.remote_mysql_user,
            password=self.settings.remote_mysql_password,
            database=self.settings.remote_mysql_database,
            charset="utf8mb4",
            autocommit=True,
            cursorclass=DictCursor,
            connect_timeout=10,
            read_timeout=20,
            write_timeout=20,
        )

    def get_status(self) -> dict[str, Any]:
        decision = get_schedule_decision()
        latest_feature_time = self._latest_created_at("features")
        latest_llm_time = self._latest_created_at("llm_outputs")
        countdown_seconds = self._seconds_until_next_run(latest_llm_time, decision.interval_seconds, decision.should_run)
        return {
            "market_session": decision.session.value,
            "should_run_now": decision.should_run,
            "interval_seconds": decision.interval_seconds,
            "countdown_seconds": countdown_seconds,
            "countdown_label": format_duration(countdown_seconds),
            "latest_feature_at": to_beijing_time(latest_feature_time),
            "latest_llm_at": to_beijing_time(latest_llm_time),
            "database_path": f"mysql://{self.settings.remote_mysql_host}:{self.settings.remote_mysql_port}/{self.settings.remote_mysql_database}",
        }

    def get_today_metrics(self, symbols: list[str]) -> dict[str, Any]:
        feature_rows = self._rows_for_today("features", ["created_at_utc"])
        llm_rows = self._rows_for_today("llm_outputs", ["input_json", "output_json", "error", "created_at_utc"])
        alert_rows = self._rows_for_today("alerts", ["created_at_utc"])
        json_only = multimodal = image_calls = llm_errors = 0
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
            feature_payload = json.loads(feature["payload_json"]) if feature else {}
            llm_input = json.loads(llm["input_json"]) if llm else {}
            llm_output = json.loads(llm["output_json"]) if llm and llm["output_json"] else {}
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
                    "chart_path": None,
                }
            )
        return states

    def get_benchmark_states(self, symbols: list[str]) -> list[dict[str, Any]]:
        states = []
        for symbol in symbols:
            feature = self._latest_row("features", symbol)
            llm = self._latest_row("llm_outputs", symbol)
            feature_payload = json.loads(feature["payload_json"]) if feature else {}
            llm_output = json.loads(llm["output_json"]) if llm and llm["output_json"] else {}
            latest_1m = self._latest_raw_candle(symbol, "1m")
            quote = _stored_quote_snapshot(feature_payload, latest_1m)
            close = quote.get("regular_market_price") or feature_payload.get("close")
            forecast = None
            if feature_payload and llm_output:
                try:
                    forecast = build_benchmark_forecast(
                        TechnicalSnapshot.model_validate(feature_payload),
                        GeminiSignal.model_validate(llm_output),
                    )
                except Exception:
                    forecast = None
            support_levels = feature_payload.get("support_levels") or []
            resistance_levels = feature_payload.get("resistance_levels") or []
            nearest_support = forecast.support_level if forecast else _nearest_level_for_view(support_levels, close, "support")
            nearest_resistance = forecast.resistance_level if forecast else _nearest_level_for_view(resistance_levels, close, "resistance")
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
                    else _outlook_note(llm_output.get("bias"), close, nearest_support, nearest_resistance, feature_payload.get("rsi_14")),
                    "upside_scenario": forecast.upside_scenario if forecast else None,
                    "downside_scenario": forecast.downside_scenario if forecast else None,
                    "rebound_scenario": forecast.rebound_scenario if forecast else None,
                }
            )
        return states

    def get_recent_llm_outputs(self, symbols: list[str], limit: int = 20) -> list[dict[str, Any]]:
        rows = self._select_symbol_rows("llm_outputs", symbols, ["symbol", "input_json", "output_json", "error", "created_at_utc"], limit)
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
        rows = self._select_symbol_rows("alerts", symbols, ["symbol", "channel", "payload_json", "created_at_utc"], limit)
        alerts = []
        for row in rows:
            payload = json.loads(row["payload_json"])
            signal = payload.get("signal", {})
            alerts.append(
                {
                    "symbol": row["symbol"],
                    "channel": row["channel"],
                    "created_at_utc": to_beijing_time(row["created_at_utc"]),
                    "bias": signal.get("bias"),
                    "sentiment_score": signal.get("sentiment_score"),
                    "confidence": signal.get("confidence"),
                    "setup_type": signal.get("setup_type"),
                }
            )
        return alerts

    def get_recent_errors(self, symbols: list[str], limit: int = 6) -> list[dict[str, Any]]:
        placeholders = ", ".join(["%s"] * len(symbols))
        with self.connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT symbol, input_json, error, created_at_utc
                    FROM llm_outputs
                    WHERE symbol IN ({placeholders}) AND error IS NOT NULL AND error != ''
                    ORDER BY source_id DESC
                    LIMIT %s
                    """,
                    [*symbols, limit],
                )
                rows = cursor.fetchall()
        return [
            {
                "symbol": row["symbol"],
                "created_at_utc": to_beijing_time(row["created_at_utc"]),
                "analysis_level": json.loads(row["input_json"]).get("analysis_level"),
                "error": row["error"],
            }
            for row in rows
        ]

    def _latest_row(self, table: str, symbol: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(f"SELECT * FROM {table} WHERE symbol = %s ORDER BY source_id DESC LIMIT 1", (symbol,))
                return cursor.fetchone()

    def _latest_raw_candle(self, symbol: str, interval: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT timestamp_utc, close, created_at_utc
                    FROM raw_candles
                    WHERE symbol = %s AND `interval` = %s
                    ORDER BY timestamp_utc DESC
                    LIMIT 1
                    """,
                    (symbol, interval),
                )
                return cursor.fetchone()

    def _latest_created_at(self, table: str) -> str | None:
        with self.connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(f"SELECT created_at_utc FROM {table} ORDER BY source_id DESC LIMIT 1")
                row = cursor.fetchone()
        return row["created_at_utc"] if row else None

    def _rows_for_today(self, table: str, columns: list[str]) -> list[dict[str, Any]]:
        selected = ", ".join(columns)
        with self.connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(f"SELECT {selected} FROM {table}")
                rows = cursor.fetchall()
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

    def _select_symbol_rows(self, table: str, symbols: list[str], columns: list[str], limit: int) -> list[dict[str, Any]]:
        if not symbols:
            return []
        placeholders = ", ".join(["%s"] * len(symbols))
        with self.connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT {", ".join(columns)}
                    FROM {table}
                    WHERE symbol IN ({placeholders})
                    ORDER BY source_id DESC
                    LIMIT %s
                    """,
                    [*symbols, limit],
                )
                return cursor.fetchall()


def _seconds_to_delta(seconds: int):
    from datetime import timedelta

    return timedelta(seconds=seconds)
