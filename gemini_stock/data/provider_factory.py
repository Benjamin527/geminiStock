from __future__ import annotations

from gemini_stock.config import Settings
from gemini_stock.data.base import MarketDataError, MarketDataProvider
from gemini_stock.data.polygon_provider import PolygonMarketDataProvider
from gemini_stock.data.yfinance_provider import YFinanceMarketDataProvider


def create_market_data_provider(settings: Settings) -> MarketDataProvider:
    if settings.data_provider == "polygon":
        if not settings.polygon_api_key:
            raise MarketDataError("POLYGON_API_KEY is required when DATA_PROVIDER=polygon")
        return PolygonMarketDataProvider(
            settings.polygon_api_key,
            base_url=settings.polygon_base_url,
            delayed_tolerance_minutes=settings.delayed_data_tolerance_minutes,
            timeout_seconds=settings.market_data_timeout_seconds,
        )
    if settings.data_provider == "auto" and settings.polygon_api_key:
        return PolygonMarketDataProvider(
            settings.polygon_api_key,
            base_url=settings.polygon_base_url,
            delayed_tolerance_minutes=settings.delayed_data_tolerance_minutes,
            timeout_seconds=settings.market_data_timeout_seconds,
        )
    return YFinanceMarketDataProvider(settings.delayed_data_tolerance_minutes)
