from datetime import datetime, timezone
from pathlib import Path

import requests

from gemini_stock.llm.openai_client import OpenAICompatibleAnalyzer
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


def _signal_payload() -> dict:
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
    ).json_dict()


class _Response:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload


def test_openai_compatible_analyzer_posts_chat_completion(monkeypatch):
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return _Response({"choices": [{"message": {"content": _signal_payload()}}]})

    monkeypatch.setattr(requests, "post", fake_post)
    analyzer = OpenAICompatibleAnalyzer(
        api_key="test-key",
        base_url="https://zjapi.com/v1",
        model="gpt-4o-mini",
    )

    signal = analyzer.analyze_json_only(_analysis_input())

    assert signal.symbol == "QQQ"
    assert calls[0][0] == "https://zjapi.com/v1/chat/completions"
    assert calls[0][1]["headers"]["Authorization"] == "Bearer test-key"
    assert calls[0][1]["json"]["model"] == "gpt-4o-mini"
    assert calls[0][1]["json"]["response_format"] == {"type": "json_object"}


def test_openai_compatible_multimodal_review_sends_image_part(monkeypatch, tmp_path):
    calls = []
    image_path = tmp_path / "chart.png"
    image_path.write_bytes(b"png-bytes")

    def fake_post(url, **kwargs):
        calls.append(kwargs["json"])
        payload = _signal_payload()
        payload["analysis_level"] = "multimodal_review"
        payload["visual_confirmation"] = "confirmed"
        return _Response({"choices": [{"message": {"content": payload}}]})

    monkeypatch.setattr(requests, "post", fake_post)
    analyzer = OpenAICompatibleAnalyzer("test-key", "https://zjapi.com/v1", "gpt-4o-mini")

    signal = analyzer.review_multimodal(
        _analysis_input(),
        GeminiSignal.model_validate(_signal_payload()),
        Path(image_path),
    )

    content = calls[0]["messages"][-1]["content"]
    assert signal.analysis_level == "multimodal_review"
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
