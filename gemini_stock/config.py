from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    symbols: list[str] = Field(default_factory=lambda: ["CONL", "TSLL"])
    benchmark_symbols: list[str] = Field(default_factory=lambda: ["SPY", "QQQ"])
    movement_alert_symbols: list[str] = Field(default_factory=lambda: ["BTC-USD"])
    data_provider: Literal["auto", "yfinance", "polygon"] = "auto"
    news_provider: Literal["none", "yfinance"] = "none"

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
    dashboard_data_source: str = "sqlite"
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
    worker_max_concurrency: int = 4

    run_once: bool = False

    @field_validator("dashboard_data_source", mode="before")
    @classmethod
    def sqlite_only_dashboard_source(cls, value: object) -> str:
        return "sqlite"


def load_settings() -> Settings:
    return Settings()
