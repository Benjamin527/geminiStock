from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    symbols: list[str] = Field(default_factory=lambda: ["CONL", "TSLL"])
    benchmark_symbols: list[str] = Field(default_factory=lambda: ["SPY", "QQQ"])
    data_provider: Literal["auto", "yfinance", "polygon"] = "yfinance"
    news_provider: Literal["none"] = "none"

    llm_provider: Literal["auto", "gemini", "openai", "fallback"] = "auto"
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-2.5-flash"
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"
    llm_fallback_on_error: bool = True

    database_path: Path = Path("data/gemini_stock.sqlite")
    chart_dir: Path = Path("charts")
    poll_interval_minutes: int = 15
    alert_cooldown_minutes: int = 60
    dashboard_data_source: Literal["sqlite", "mysql"] = "sqlite"
    positions: dict[str, float] = Field(default_factory=dict)
    average_costs: dict[str, float] = Field(default_factory=dict)

    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    feishu_webhook_url: str | None = None
    wecom_webhook_url: str | None = None

    yfinance_period_1m: str = "5d"
    yfinance_period_15m: str = "10d"
    delayed_data_tolerance_minutes: int = 30
    polygon_api_key: str | None = None
    polygon_base_url: str = "https://api.polygon.io"
    market_data_timeout_seconds: int = 20
    worker_stale_after_intervals: float = 2.5
    max_scheduler_sleep_seconds: int = 300

    sync_remote_mysql: bool = False
    remote_mysql_host: str | None = None
    remote_mysql_port: int = 3306
    remote_mysql_user: str | None = None
    remote_mysql_password: str | None = None
    remote_mysql_database: str = "gemini_stock"

    run_once: bool = False


def load_settings() -> Settings:
    return Settings()
