from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from gemini_stock.schemas import Candle, GeminiSignal, NewsItem, TechnicalSnapshot
from gemini_stock.rules.movement_alerts import DEFAULT_MOVEMENT_ALERT_THRESHOLD_PCTS


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
                    analysis_level TEXT,
                    snapshot_timestamp_utc TEXT,
                    has_image INTEGER NOT NULL DEFAULT 0,
                    input_json TEXT NOT NULL,
                    output_json TEXT,
                    error TEXT,
                    created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                );

                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    event_key TEXT,
                    alert_type TEXT,
                    payload_json TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                );

                CREATE TABLE IF NOT EXISTS watchlist (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    profile TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                    UNIQUE(symbol, profile)
                );

                CREATE TABLE IF NOT EXISTS movement_alert_settings (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    tier1_pct REAL NOT NULL,
                    tier2_pct REAL NOT NULL,
                    tier3_pct REAL NOT NULL,
                    rise_tier1_pct REAL,
                    rise_tier2_pct REAL,
                    rise_tier3_pct REAL,
                    updated_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                );
                """
            )
            self._ensure_column(conn, "llm_outputs", "analysis_level", "TEXT")
            self._ensure_column(conn, "llm_outputs", "snapshot_timestamp_utc", "TEXT")
            self._ensure_column(conn, "llm_outputs", "has_image", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "alerts", "event_key", "TEXT")
            self._ensure_column(conn, "alerts", "alert_type", "TEXT")
            self._ensure_column(conn, "movement_alert_settings", "rise_tier1_pct", "REAL")
            self._ensure_column(conn, "movement_alert_settings", "rise_tier2_pct", "REAL")
            self._ensure_column(conn, "movement_alert_settings", "rise_tier3_pct", "REAL")
            conn.executescript(
                """
                CREATE INDEX IF NOT EXISTS idx_raw_candles_symbol_interval_timestamp
                    ON raw_candles(symbol, interval, timestamp_utc DESC);
                CREATE INDEX IF NOT EXISTS idx_features_symbol_timestamp
                    ON features(symbol, timestamp_utc DESC);
                CREATE INDEX IF NOT EXISTS idx_llm_outputs_symbol_snapshot
                    ON llm_outputs(symbol, snapshot_timestamp_utc DESC);
                CREATE INDEX IF NOT EXISTS idx_llm_outputs_symbol_created
                    ON llm_outputs(symbol, created_at_utc DESC);
                CREATE INDEX IF NOT EXISTS idx_alerts_channel_event_key
                    ON alerts(channel, event_key);
                CREATE INDEX IF NOT EXISTS idx_alerts_symbol_channel_type_created
                    ON alerts(symbol, channel, alert_type, created_at_utc DESC);
                CREATE INDEX IF NOT EXISTS idx_watchlist_profile_enabled
                    ON watchlist(profile, enabled, created_at_utc DESC);
                """
            )
            self._backfill_llm_metadata(conn)
            self._backfill_alert_metadata(conn)
            self._ensure_default_movement_alert_settings(conn)

    @staticmethod
    def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    @staticmethod
    def _backfill_alert_metadata(conn: sqlite3.Connection) -> None:
        rows = conn.execute(
            """
            SELECT id, payload_json
            FROM alerts
            WHERE event_key IS NULL OR alert_type IS NULL
            """
        ).fetchall()
        for row in rows:
            event_key, alert_type = _extract_alert_metadata(row["payload_json"])
            conn.execute(
                """
                UPDATE alerts
                SET event_key = COALESCE(event_key, ?),
                    alert_type = COALESCE(alert_type, ?)
                WHERE id = ?
                """,
                (event_key, alert_type, row["id"]),
            )

    @staticmethod
    def _backfill_llm_metadata(conn: sqlite3.Connection) -> None:
        rows = conn.execute(
            """
            SELECT id, input_json
            FROM llm_outputs
            WHERE analysis_level IS NULL OR snapshot_timestamp_utc IS NULL
            """
        ).fetchall()
        for row in rows:
            analysis_level, snapshot_timestamp, has_image = _extract_llm_metadata(row["input_json"])
            conn.execute(
                """
                UPDATE llm_outputs
                SET analysis_level = COALESCE(analysis_level, ?),
                    snapshot_timestamp_utc = COALESCE(snapshot_timestamp_utc, ?),
                    has_image = ?
                WHERE id = ?
                """,
                (analysis_level, snapshot_timestamp, has_image, row["id"]),
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
        snapshot_timestamp = _extract_snapshot_timestamp(input_payload)
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO llm_outputs
                    (symbol, analysis_level, snapshot_timestamp_utc, has_image, input_json, output_json, error)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    symbol,
                    input_payload.get("analysis_level"),
                    snapshot_timestamp,
                    1 if input_payload.get("has_image") else 0,
                    json.dumps(input_payload, ensure_ascii=False, default=str),
                    json.dumps(output_payload, ensure_ascii=False, default=str) if output_payload is not None else None,
                    error,
                ),
            )

    def save_alert(self, symbol: str, payload: dict[str, Any], channel: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO alerts (symbol, channel, event_key, alert_type, payload_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    symbol,
                    channel,
                    payload.get("event_key"),
                    payload.get("type"),
                    json.dumps(payload, ensure_ascii=False, default=str),
                ),
            )

    def get_movement_alert_thresholds(self, event_type: str | None = None) -> list[float]:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT tier1_pct, tier2_pct, tier3_pct, rise_tier1_pct, rise_tier2_pct, rise_tier3_pct
                FROM movement_alert_settings
                WHERE id = 1
                """
            ).fetchone()
        if row is None:
            return list(DEFAULT_MOVEMENT_ALERT_THRESHOLD_PCTS)
        if event_type == "fast_rise":
            return [
                float(row["rise_tier1_pct"] if row["rise_tier1_pct"] is not None else row["tier1_pct"]),
                float(row["rise_tier2_pct"] if row["rise_tier2_pct"] is not None else row["tier2_pct"]),
                float(row["rise_tier3_pct"] if row["rise_tier3_pct"] is not None else row["tier3_pct"]),
            ]
        return [float(row["tier1_pct"]), float(row["tier2_pct"]), float(row["tier3_pct"])]

    def get_movement_alert_threshold_settings(self) -> dict[str, list[float]]:
        return {
            "fast_drop": self.get_movement_alert_thresholds("fast_drop"),
            "fast_rise": self.get_movement_alert_thresholds("fast_rise"),
        }

    def save_movement_alert_thresholds(self, thresholds: Iterable[float] | dict[str, Iterable[float]]) -> list[float] | dict[str, list[float]]:
        if isinstance(thresholds, dict):
            drop = _normalize_movement_thresholds(thresholds.get("fast_drop") or thresholds.get("drop") or self.get_movement_alert_thresholds("fast_drop"))
            rise = _normalize_movement_thresholds(thresholds.get("fast_rise") or thresholds.get("rise") or self.get_movement_alert_thresholds("fast_rise"))
            with self.connect() as conn:
                conn.execute(
                    """
                    INSERT INTO movement_alert_settings
                        (id, tier1_pct, tier2_pct, tier3_pct, rise_tier1_pct, rise_tier2_pct, rise_tier3_pct, updated_at_utc)
                    VALUES (1, ?, ?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                    ON CONFLICT(id) DO UPDATE SET
                        tier1_pct = excluded.tier1_pct,
                        tier2_pct = excluded.tier2_pct,
                        tier3_pct = excluded.tier3_pct,
                        rise_tier1_pct = excluded.rise_tier1_pct,
                        rise_tier2_pct = excluded.rise_tier2_pct,
                        rise_tier3_pct = excluded.rise_tier3_pct,
                        updated_at_utc = excluded.updated_at_utc
                    """,
                    (*drop, *rise),
                )
            return {"fast_drop": drop, "fast_rise": rise}

        normalized = _normalize_movement_thresholds(thresholds)
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO movement_alert_settings (id, tier1_pct, tier2_pct, tier3_pct, rise_tier1_pct, rise_tier2_pct, rise_tier3_pct, updated_at_utc)
                VALUES (1, ?, ?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                ON CONFLICT(id) DO UPDATE SET
                    tier1_pct = excluded.tier1_pct,
                    tier2_pct = excluded.tier2_pct,
                    tier3_pct = excluded.tier3_pct,
                    rise_tier1_pct = excluded.rise_tier1_pct,
                    rise_tier2_pct = excluded.rise_tier2_pct,
                    rise_tier3_pct = excluded.rise_tier3_pct,
                    updated_at_utc = excluded.updated_at_utc
                """,
                (*normalized, *normalized),
            )
        return normalized

    def has_alert_event(self, channel: str, event_key: str) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT 1
                FROM alerts
                WHERE channel = ? AND event_key = ?
                LIMIT 1
                """,
                (channel, event_key),
            ).fetchone()
        return row is not None

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
            row = conn.execute(
                """
                SELECT 1
                FROM alerts
                WHERE symbol = ? AND channel = ? AND alert_type = ? AND created_at_utc >= ?
                LIMIT 1
                """,
                (symbol, channel, alert_type, cutoff.isoformat()),
            ).fetchone()
        return row is not None

    def has_any_recent_alert(self, now: datetime, within_minutes: int) -> bool:
        cutoff = now.astimezone(timezone.utc) - timedelta(minutes=within_minutes)
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT 1
                FROM alerts
                WHERE created_at_utc >= ?
                LIMIT 1
                """,
                (cutoff.isoformat(),),
            ).fetchone()
        return row is not None

    def get_latest_llm_output_time(self) -> str | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT created_at_utc
                FROM llm_outputs
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()
        return str(row["created_at_utc"]) if row and row["created_at_utc"] else None

    def get_latest_candle_time(self, symbol: str, interval: str) -> datetime | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT timestamp_utc
                FROM raw_candles
                WHERE symbol = ? AND interval = ?
                ORDER BY timestamp_utc DESC
                LIMIT 1
                """,
                (symbol, interval),
            ).fetchone()
        if row is None or not row["timestamp_utc"]:
            return None
        return _parse_datetime(str(row["timestamp_utc"]))

    def get_successful_signal_for_snapshot(self, symbol: str, snapshot_timestamp: datetime) -> GeminiSignal | None:
        expected = snapshot_timestamp.astimezone(timezone.utc).isoformat()
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT output_json
                FROM llm_outputs
                WHERE symbol = ?
                    AND snapshot_timestamp_utc = ?
                    AND output_json IS NOT NULL
                    AND (error IS NULL OR error = '')
                ORDER BY id DESC
                LIMIT 1
                """,
                (symbol, expected),
            ).fetchall()
        for row in rows:
            try:
                output_payload = json.loads(row["output_json"])
            except Exception:
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
        allowed = {"raw_candles", "features", "news", "llm_outputs", "alerts", "watchlist"}
        if table not in allowed:
            raise ValueError(f"unsupported table: {table}")
        with self.connect() as conn:
            return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

    def list_watch_symbols(self, profile: str = "primary") -> list[str]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT symbol
                FROM watchlist
                WHERE profile = ? AND enabled = 1
                ORDER BY created_at_utc ASC, symbol ASC
                """,
                (profile,),
            ).fetchall()
        return [str(row["symbol"]).upper() for row in rows]

    def add_watch_symbol(self, symbol: str, profile: str = "primary") -> str:
        normalized = _normalize_symbol(symbol)
        if not normalized:
            raise ValueError("symbol is required")
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO watchlist (symbol, profile, enabled)
                VALUES (?, ?, 1)
                ON CONFLICT(symbol, profile) DO UPDATE SET enabled = 1
                """,
                (normalized, profile),
            )
        return normalized

    @staticmethod
    def _ensure_default_movement_alert_settings(conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            INSERT OR IGNORE INTO movement_alert_settings
                (id, tier1_pct, tier2_pct, tier3_pct, rise_tier1_pct, rise_tier2_pct, rise_tier3_pct)
            VALUES (1, ?, ?, ?, ?, ?, ?)
            """,
            (*DEFAULT_MOVEMENT_ALERT_THRESHOLD_PCTS, *DEFAULT_MOVEMENT_ALERT_THRESHOLD_PCTS),
        )
        conn.execute(
            """
            UPDATE movement_alert_settings
            SET rise_tier1_pct = COALESCE(rise_tier1_pct, tier1_pct),
                rise_tier2_pct = COALESCE(rise_tier2_pct, tier2_pct),
                rise_tier3_pct = COALESCE(rise_tier3_pct, tier3_pct)
            WHERE id = 1
            """
        )


def _parse_datetime(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def _extract_snapshot_timestamp(input_payload: dict[str, Any]) -> str | None:
    technical_snapshot = input_payload.get("technical_snapshot")
    if isinstance(technical_snapshot, dict):
        timestamp = technical_snapshot.get("timestamp_utc")
        if timestamp:
            parsed = _parse_datetime(str(timestamp))
            return parsed.isoformat() if parsed else str(timestamp)
    return None


def _extract_alert_metadata(payload_json: str) -> tuple[str | None, str | None]:
    try:
        payload = json.loads(payload_json)
    except Exception:
        return None, None
    return payload.get("event_key"), payload.get("type")


def _extract_llm_metadata(input_json: str) -> tuple[str | None, str | None, int]:
    try:
        input_payload = json.loads(input_json)
    except Exception:
        return None, None, 0
    return (
        input_payload.get("analysis_level"),
        _extract_snapshot_timestamp(input_payload),
        1 if input_payload.get("has_image") else 0,
    )


def _normalize_symbol(value: str) -> str:
    return "".join(ch for ch in value.upper().strip() if ch.isalnum() or ch in {".", "-", "_"})


def _normalize_movement_thresholds(values: Iterable[float]) -> list[float]:
    thresholds = [round(float(value), 4) for value in values]
    if len(thresholds) != 3:
        raise ValueError("movement alert thresholds must contain exactly 3 values")
    if any(value <= 0 for value in thresholds):
        raise ValueError("movement alert thresholds must be positive")
    if thresholds != sorted(thresholds) or len(set(thresholds)) != 3:
        raise ValueError("movement alert thresholds must be strictly ascending")
    return thresholds
