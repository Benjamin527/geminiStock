from datetime import datetime, timezone

import pytest

from gemini_stock.config import Settings
from gemini_stock.llm.gemini_client import GeminiAnalysisError, RuleBasedFallbackAnalyzer
from gemini_stock.main import analyze_json_only_with_fallback
from gemini_stock.schemas import TechnicalAnalysisInput


class FailingAnalyzer:
    def analyze_json_only(self, analysis_input):
        raise GeminiAnalysisError("upstream timeout")


def _analysis_input() -> TechnicalAnalysisInput:
    return TechnicalAnalysisInput(
        symbol="TQQQ",
        timestamp_utc=datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc),
        last_price=100,
        ohlcv_recent=[],
        rsi_14=45,
        macd=0.1,
        macd_signal=0.2,
        macd_histogram=-0.1,
        macd_histogram_last_3=[-0.2, -0.1, -0.05],
        ema_20=101,
        ema_50=99,
        vwap=100,
        atr_14=1.5,
        support_levels=[98],
        resistance_levels=[103],
        volume_regime="normal",
        trend_regime="mixed",
        technical_events=["price_near_support"],
        news_summary=[],
    )


def test_json_analysis_falls_back_to_rule_based_signal_when_enabled():
    signal, error = analyze_json_only_with_fallback(
        FailingAnalyzer(),
        RuleBasedFallbackAnalyzer(),
        _analysis_input(),
        Settings(llm_fallback_on_error=True),
    )

    assert signal.symbol == "TQQQ"
    assert signal.analysis_level == "json_only"
    assert "upstream timeout" in error


def test_rule_based_fallback_does_not_emit_trade_alerts():
    signal = RuleBasedFallbackAnalyzer().analyze_json_only(
        _analysis_input().model_copy(update={"rsi_14": 25, "last_price": 100, "ema_50": 99, "atr_14": 1.5})
    )

    assert signal.should_alert is False
    assert signal.setup_type == "no_trade"
    assert signal.confidence <= 0.5


def test_json_analysis_raises_when_fallback_is_disabled():
    with pytest.raises(GeminiAnalysisError):
        analyze_json_only_with_fallback(
            FailingAnalyzer(),
            RuleBasedFallbackAnalyzer(),
            _analysis_input(),
            Settings(llm_fallback_on_error=False),
        )
