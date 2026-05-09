from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from gemini_stock.schemas import Candle, GeminiSignal, NewsItem, TechnicalSnapshot


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def initialize(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS raw_candles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    interval TEXT NOT NULL,
                    timestamp_utc TEXT NOT NULL,
                    open REAL NOT NULL,
                    high REAL NOT NULL,
                    low REAL NOT NULL,
                    close REAL NOT NULL,
                    volume INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                    UNIQUE(symbol, interval, timestamp_utc)
                );

                CREATE TABLE IF NOT EXISTS features (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    interval TEXT NOT NULL,
                    timestamp_utc TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                );

                CREATE TABLE IF NOT EXISTS news (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    source TEXT NOT NULL,
                    published_at TEXT NOT NULL,
                    url TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                    UNIQUE(url)
                );

                CREATE TABLE IF NOT EXISTS llm_outputs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    input_json TEXT NOT NULL,
                    output_json TEXT,
                    error TEXT,
                    created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                );

                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                );
                """
            )

    def save_candles(self, candles: Iterable[Candle]) -> None:
        rows = [
            (
                candle.symbol,
                candle.interval,
                candle.timestamp_utc.isoformat(),
                candle.open,
                candle.high,
                candle.low,
                candle.close,
                candle.volume,
                json.dumps(candle.json_dict(), ensure_ascii=False),
            )
            for candle in candles
        ]
        if not rows:
            return
        with self.connect() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO raw_candles
                (symbol, interval, timestamp_utc, open, high, low, close, volume, payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    def save_feature(self, snapshot: TechnicalSnapshot) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO features (symbol, interval, timestamp_utc, payload_json)
                VALUES (?, ?, ?, ?)
                """,
                (
                    snapshot.symbol,
                    snapshot.interval,
                    snapshot.timestamp_utc.isoformat(),
                    json.dumps(snapshot.json_dict(), ensure_ascii=False),
                ),
            )

    def save_news(self, news_items: Iterable[NewsItem]) -> None:
        rows = [
            (
                item.title,
                item.source,
                item.published_at.isoformat(),
                str(item.url),
                json.dumps(item.json_dict(), ensure_ascii=False),
            )
            for item in news_items
        ]
        if not rows:
            return
        with self.connect() as conn:
            conn.executemany(
                """
                INSERT OR IGNORE INTO news (title, source, published_at, url, payload_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                rows,
            )

    def save_llm_output(
        self,
        symbol: str,
        input_payload: dict[str, Any],
        output_payload: dict[str, Any] | None,
        error: str | None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO llm_outputs (symbol, input_json, output_json, error)
                VALUES (?, ?, ?, ?)
                """,
                (
                    symbol,
                    json.dumps(input_payload, ensure_ascii=False, default=str),
                    json.dumps(output_payload, ensure_ascii=False, default=str) if output_payload is not None else None,
                    error,
                ),
            )

    def save_alert(self, symbol: str, payload: dict[str, Any], channel: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO alerts (symbol, channel, payload_json)
                VALUES (?, ?, ?)
                """,
                (symbol, channel, json.dumps(payload, ensure_ascii=False, default=str)),
            )

    def has_alert_event(self, channel: str, event_key: str) -> bool:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT payload_json
                FROM alerts
                WHERE channel = ?
                ORDER BY id DESC
                LIMIT 200
                """,
                (channel,),
            ).fetchall()
        for row in rows:
            try:
                payload = json.loads(row["payload_json"])
            except Exception:
                continue
            if payload.get("event_key") == event_key:
                return True
        return False

    def has_recent_alert_type(
        self,
        symbol: str,
        channel: str,
        alert_type: str,
        now: datetime,
        within_minutes: int,
    ) -> bool:
        cutoff = now.astimezone(timezone.utc) - timedelta(minutes=within_minutes)
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT payload_json, created_at_utc
                FROM alerts
                WHERE symbol = ? AND channel = ?
                ORDER BY id DESC
                LIMIT 200
                """,
                (symbol, channel),
            ).fetchall()
        for row in rows:
            created_at = _parse_datetime(row["created_at_utc"])
            if created_at is None or created_at < cutoff:
                continue
            try:
                payload = json.loads(row["payload_json"])
            except Exception:
                continue
            if payload.get("type") == alert_type:
                return True
        return False

    def get_successful_signal_for_snapshot(self, symbol: str, snapshot_timestamp: datetime) -> GeminiSignal | None:
        expected = snapshot_timestamp.astimezone(timezone.utc)
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT input_json, output_json
                FROM llm_outputs
                WHERE symbol = ? AND output_json IS NOT NULL AND (error IS NULL OR error = '')
                ORDER BY id DESC
                LIMIT 100
                """,
                (symbol,),
            ).fetchall()
        for row in rows:
            try:
                input_payload = json.loads(row["input_json"])
                output_payload = json.loads(row["output_json"])
            except Exception:
                continue
            technical_snapshot = input_payload.get("technical_snapshot") or {}
            timestamp = technical_snapshot.get("timestamp_utc")
            parsed = _parse_datetime(timestamp) if timestamp else None
            if parsed is None or parsed != expected:
                continue
            return GeminiSignal.model_validate(output_payload)
        return None

    def get_successful_signals_for_trading_date(self, symbols: list[str], trading_date: date) -> list[GeminiSignal]:
        if not symbols:
            return []
        symbol_set = set(symbols)
        start = datetime.combine(trading_date, datetime.min.time(), tzinfo=timezone.utc)
        end = start + timedelta(days=1)
        placeholders = ",".join("?" for _ in symbols)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT symbol, output_json
                FROM llm_outputs
                WHERE symbol IN ({placeholders})
                    AND output_json IS NOT NULL
                    AND (error IS NULL OR error = '')
                ORDER BY id ASC
                """,
                tuple(symbols),
            ).fetchall()
        signals: list[GeminiSignal] = []
        seen: set[tuple[str, datetime, str]] = set()
        for row in rows:
            try:
                signal = GeminiSignal.model_validate(json.loads(row["output_json"]))
            except Exception:
                continue
            timestamp = signal.timestamp_utc.astimezone(timezone.utc)
            if signal.symbol not in symbol_set or not (start <= timestamp < end):
                continue
            key = (signal.symbol, timestamp, signal.analysis_level)
            if key in seen:
                continue
            seen.add(key)
            signals.append(signal)
        return signals

    def get_candles_for_trading_date(self, symbol: str, interval: str, trading_date: date) -> list[Candle]:
        start = datetime.combine(trading_date, datetime.min.time(), tzinfo=timezone.utc)
        end = start + timedelta(days=1)
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT payload_json
                FROM raw_candles
                WHERE symbol = ? AND interval = ? AND timestamp_utc >= ? AND timestamp_utc < ?
                ORDER BY timestamp_utc ASC
                """,
                (symbol, interval, start.isoformat(), end.isoformat()),
            ).fetchall()
        candles: list[Candle] = []
        for row in rows:
            try:
                candles.append(Candle.model_validate(json.loads(row["payload_json"])))
            except Exception:
                continue
        return candles

    def count(self, table: str) -> int:
        allowed = {"raw_candles", "features", "news", "llm_outputs", "alerts"}
        if table not in allowed:
            raise ValueError(f"unsupported table: {table}")
        with self.connect() as conn:
            return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _parse_datetime(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None
