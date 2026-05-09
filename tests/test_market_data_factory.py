from gemini_stock.config import Settings
from gemini_stock.data.provider_factory import create_market_data_provider
from gemini_stock.data.polygon_provider import PolygonMarketDataProvider
from gemini_stock.data.yfinance_provider import YFinanceMarketDataProvider


def test_market_data_factory_uses_yfinance_by_default():
    provider = create_market_data_provider(Settings(data_provider="yfinance"))

    assert isinstance(provider, YFinanceMarketDataProvider)


def test_market_data_factory_can_select_polygon_when_key_is_configured():
    provider = create_market_data_provider(Settings(data_provider="polygon", polygon_api_key="test-key"))

    assert isinstance(provider, PolygonMarketDataProvider)


def test_market_data_factory_auto_falls_back_to_yfinance_without_polygon_key():
    provider = create_market_data_provider(Settings(data_provider="auto", polygon_api_key=None))

    assert isinstance(provider, YFinanceMarketDataProvider)
