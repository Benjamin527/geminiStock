from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pymysql
from pymysql.cursors import DictCursor


@dataclass(frozen=True)
class MySQLSyncConfig:
    host: str
    port: int
    user: str
    password: str
    database: str


class MySQLSync:
    def __init__(self, config: MySQLSyncConfig) -> None:
        self.config = config

    def connect(self, *, include_database: bool = True):
        kwargs: dict[str, Any] = {
            "host": self.config.host,
            "port": self.config.port,
            "user": self.config.user,
            "password": self.config.password,
            "charset": "utf8mb4",
            "autocommit": True,
            "cursorclass": DictCursor,
            "connect_timeout": 10,
            "read_timeout": 20,
            "write_timeout": 20,
        }
        if include_database:
            kwargs["database"] = self.config.database
        return pymysql.connect(**kwargs)

    def initialize(self) -> None:
        with self.connect(include_database=False) as conn:
            with conn.cursor() as cursor:
                cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{self.config.database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
        with self.connect() as conn:
            with conn.cursor() as cursor:
                for ddl in _TABLE_DDLS:
                    cursor.execute(ddl)

    def sync_from_sqlite(self, sqlite_path: str | Path) -> dict[str, int]:
        self.initialize()
        sqlite_conn = sqlite3.connect(Path(sqlite_path))
        sqlite_conn.row_factory = sqlite3.Row
        inserted: dict[str, int] = {}
        try:
            with self.connect() as mysql_conn:
                with mysql_conn.cursor() as cursor:
                    for table, columns in _TABLE_COLUMNS.items():
                        cursor.execute(f"SELECT COALESCE(MAX(source_id), 0) AS max_source_id FROM `{table}`")
                        result = cursor.fetchone() or {}
                        max_source_id = int(result.get("max_source_id") or 0)
                        rows = sqlite_conn.execute(f"SELECT * FROM {table} WHERE id > ? ORDER BY id ASC", (max_source_id,)).fetchall()
                        if not rows:
                            inserted[table] = 0
                            continue
                        values = [tuple([int(row["id"])] + [row[column] for column in columns]) for row in rows]
                        placeholders = ", ".join(["%s"] * (1 + len(columns)))
                        update_clause = ", ".join(f"`{column}`=VALUES(`{column}`)" for column in columns)
                        cursor.executemany(
                            f"""
                            INSERT INTO `{table}` (`source_id`, {", ".join(f"`{column}`" for column in columns)})
                            VALUES ({placeholders})
                            ON DUPLICATE KEY UPDATE {update_clause}
                            """,
                            values,
                        )
                        inserted[table] = len(values)
        finally:
            sqlite_conn.close()
        return inserted

    def test_connection(self) -> str:
        self.initialize()
        with self.connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT DATABASE() AS database_name")
                row = cursor.fetchone() or {}
        return str(row.get("database_name") or self.config.database)


_TABLE_COLUMNS: dict[str, list[str]] = {
    "raw_candles": [
        "symbol",
        "interval",
        "timestamp_utc",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "payload_json",
        "created_at_utc",
    ],
    "features": ["symbol", "interval", "timestamp_utc", "payload_json", "created_at_utc"],
    "news": ["title", "source", "published_at", "url", "payload_json", "created_at_utc"],
    "llm_outputs": ["symbol", "input_json", "output_json", "error", "created_at_utc"],
    "alerts": ["symbol", "channel", "payload_json", "created_at_utc"],
}


_TABLE_DDLS = [
    """
    CREATE TABLE IF NOT EXISTS `raw_candles` (
        `id` BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
        `source_id` BIGINT NOT NULL UNIQUE,
        `symbol` VARCHAR(32) NOT NULL,
        `interval` VARCHAR(8) NOT NULL,
        `timestamp_utc` VARCHAR(40) NOT NULL,
        `open` DOUBLE NOT NULL,
        `high` DOUBLE NOT NULL,
        `low` DOUBLE NOT NULL,
        `close` DOUBLE NOT NULL,
        `volume` BIGINT NOT NULL,
        `payload_json` LONGTEXT NOT NULL,
        `created_at_utc` VARCHAR(40) NOT NULL,
        UNIQUE KEY `uniq_candle` (`symbol`, `interval`, `timestamp_utc`),
        KEY `idx_raw_candles_symbol` (`symbol`),
        KEY `idx_raw_candles_created` (`created_at_utc`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS `features` (
        `id` BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
        `source_id` BIGINT NOT NULL UNIQUE,
        `symbol` VARCHAR(32) NOT NULL,
        `interval` VARCHAR(8) NOT NULL,
        `timestamp_utc` VARCHAR(40) NOT NULL,
        `payload_json` LONGTEXT NOT NULL,
        `created_at_utc` VARCHAR(40) NOT NULL,
        KEY `idx_features_symbol` (`symbol`),
        KEY `idx_features_created` (`created_at_utc`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS `news` (
        `id` BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
        `source_id` BIGINT NOT NULL UNIQUE,
        `title` VARCHAR(512) NOT NULL,
        `source` VARCHAR(128) NOT NULL,
        `published_at` VARCHAR(40) NOT NULL,
        `url` VARCHAR(1024) NOT NULL,
        `payload_json` LONGTEXT NOT NULL,
        `created_at_utc` VARCHAR(40) NOT NULL,
        UNIQUE KEY `uniq_news_url` (`url`(255)),
        KEY `idx_news_created` (`created_at_utc`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS `llm_outputs` (
        `id` BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
        `source_id` BIGINT NOT NULL UNIQUE,
        `symbol` VARCHAR(32) NOT NULL,
        `input_json` LONGTEXT NOT NULL,
        `output_json` LONGTEXT NULL,
        `error` LONGTEXT NULL,
        `created_at_utc` VARCHAR(40) NOT NULL,
        KEY `idx_llm_outputs_symbol` (`symbol`),
        KEY `idx_llm_outputs_created` (`created_at_utc`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS `alerts` (
        `id` BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
        `source_id` BIGINT NOT NULL UNIQUE,
        `symbol` VARCHAR(64) NOT NULL,
        `channel` VARCHAR(32) NOT NULL,
        `payload_json` LONGTEXT NOT NULL,
        `created_at_utc` VARCHAR(40) NOT NULL,
        KEY `idx_alerts_symbol` (`symbol`),
        KEY `idx_alerts_created` (`created_at_utc`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
]
