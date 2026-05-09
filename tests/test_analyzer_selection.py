from gemini_stock.config import Settings
from gemini_stock.llm.gemini_client import GeminiAnalyzer, RuleBasedFallbackAnalyzer
from gemini_stock.llm.openai_client import OpenAICompatibleAnalyzer
from gemini_stock.main import create_analyzer


def test_create_analyzer_prefers_explicit_openai_provider():
    settings = Settings(
        llm_provider="openai",
        openai_api_key="test-key",
        openai_base_url="https://zjapi.com/v1",
        openai_model="gpt-4o-mini",
        gemini_api_key="gemini-key",
    )

    analyzer = create_analyzer(settings)

    assert isinstance(analyzer, OpenAICompatibleAnalyzer)


def test_create_analyzer_keeps_gemini_provider_when_configured():
    settings = Settings(llm_provider="gemini", gemini_api_key="gemini-key")

    analyzer = create_analyzer(settings)

    assert isinstance(analyzer, GeminiAnalyzer)


def test_create_analyzer_uses_fallback_without_keys():
    settings = Settings(llm_provider="auto", gemini_api_key=None, openai_api_key=None)

    analyzer = create_analyzer(settings)

    assert isinstance(analyzer, RuleBasedFallbackAnalyzer)
