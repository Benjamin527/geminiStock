from datetime import datetime, timezone

from gemini_stock.llm.prompts import build_json_only_prompt, build_multimodal_review_prompt
from gemini_stock.schemas import GeminiSignal, OHLCVSummary, TechnicalAnalysisInput


def _analysis_input() -> TechnicalAnalysisInput:
    now = datetime.now(timezone.utc)
    return TechnicalAnalysisInput(
        symbol="QQQ",
        timeframe="15m",
        timestamp_utc=now,
        last_price=430.5,
        ohlcv_recent=[
            OHLCVSummary(timestamp_utc=now, open=429, high=431, low=428, close=430.5, volume=1000)
        ],
        rsi_14=32,
        macd=0.2,
        macd_signal=0.1,
        macd_histogram=0.1,
        macd_histogram_last_3=[-0.1, 0.0, 0.1],
        ema_20=431,
        ema_50=432,
        vwap=430,
        atr_14=2,
        support_levels=[428],
        resistance_levels=[434],
        volume_regime="normal",
        trend_regime="mixed",
        technical_events=["rsi_oversold", "price_above_vwap"],
        news_summary=["No high relevance news."],
    )


def _signal() -> GeminiSignal:
    return GeminiSignal(
        symbol="QQQ",
        timestamp_utc=datetime.now(timezone.utc),
        analysis_level="json_only",
        bias="bullish",
        sentiment_score=6.5,
        confidence=0.7,
        setup_type="bullish_reversal",
        visual_confirmation="not_applicable",
        should_alert=True,
        entry_zone=[429, 431],
        stop_loss=426,
        take_profit=[435, 438],
        risk_reward_ratio=1.8,
        reasons=["test"],
        risk_warnings=["test"],
    )


def test_json_only_prompt_has_no_image_reference():
    prompt = build_json_only_prompt(_analysis_input())

    assert '"analysis_level": "json_only"' in prompt
    assert "technical_events" in prompt
    assert "news_summary" in prompt
    assert "image_path" not in prompt
    assert "simplified_chart_image_path" not in prompt


def test_multimodal_review_prompt_includes_preliminary_signal_but_no_indicator_recalculation():
    prompt = build_multimodal_review_prompt(_analysis_input(), _signal())

    assert '"analysis_level": "multimodal_review"' in prompt
    assert "preliminary_json_only_signal" in prompt
    assert "Do not recalculate RSI" in prompt


def test_prompts_include_zhaoge_decision_framework():
    prompt = build_json_only_prompt(_analysis_input())

    assert "赵哥" in prompt
    assert "二次握手" in prompt
    assert "失效条件" in prompt
    assert "不追价区" in prompt
    assert "仓位约束" in prompt
    assert "模式" in prompt
    assert "3:30 pm" in prompt
    assert "被动减仓" in prompt
    assert "zhaoge_context" in prompt
