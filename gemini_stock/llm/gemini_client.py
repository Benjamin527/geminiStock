from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from google import genai
from google.genai import types
from pydantic import ValidationError

from gemini_stock.llm.prompts import build_json_only_prompt, build_multimodal_review_prompt
from gemini_stock.schemas import GeminiSignal, TechnicalAnalysisInput

logger = logging.getLogger(__name__)


class GeminiAnalysisError(RuntimeError):
    pass


class GeminiAnalyzer:
    def __init__(self, api_key: str | None, model: str) -> None:
        if not api_key:
            raise GeminiAnalysisError("GEMINI_API_KEY is required when Gemini analysis is enabled")
        self.client = genai.Client(api_key=api_key)
        self.model = model

    def analyze_json_only(self, analysis_input: TechnicalAnalysisInput) -> GeminiSignal:
        prompt = build_json_only_prompt(analysis_input)
        return self._generate_signal([prompt], analysis_input.symbol)

    def review_multimodal(
        self,
        analysis_input: TechnicalAnalysisInput,
        preliminary_signal: GeminiSignal,
        image_path: str | Path,
    ) -> GeminiSignal:
        prompt = build_multimodal_review_prompt(analysis_input, preliminary_signal)
        image = Path(image_path)
        input_parts: list[Any] = [prompt]
        input_parts.append(types.Part.from_bytes(data=image.read_bytes(), mime_type="image/png"))
        return self._generate_signal(input_parts, analysis_input.symbol)

    def _generate_signal(self, input_parts: list[Any], symbol: str) -> GeminiSignal:
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                response = self.client.models.generate_content(
                    model=self.model,
                    contents=input_parts,
                    config={
                        "response_mime_type": "application/json",
                        "response_json_schema": GeminiSignal.model_json_schema(),
                    },
                )
                raw_text = response.text or ""
                payload = json.loads(raw_text)
                return GeminiSignal.model_validate(payload)
            except (json.JSONDecodeError, ValidationError, Exception) as exc:
                last_error = exc
                logger.warning(
                    "gemini_analysis_attempt_failed",
                    extra={"symbol": symbol, "attempt": attempt + 1, "error": str(exc)},
                )
        raise GeminiAnalysisError(f"Gemini returned invalid JSON after retry: {last_error}")


class RuleBasedFallbackAnalyzer:
    """Offline fallback for local smoke runs when GEMINI_API_KEY is not set."""

    def analyze_json_only(self, analysis_input: TechnicalAnalysisInput) -> GeminiSignal:
        score = 0.0
        bias = "neutral"
        rsi = analysis_input.rsi_14
        if rsi is not None and rsi < 35 and analysis_input.last_price >= (analysis_input.ema_50 or analysis_input.last_price) - 2 * (analysis_input.atr_14 or 0):
            score = 7.2
            bias = "bullish"
        l1 = min((bar.low for bar in analysis_input.ohlcv_recent[-8:]), default=analysis_input.last_price)
        reasons = [
            "本地兜底只给观察框架：先判断模式是否切换，再记录 L1，优先等待二次握手。",
        ]
        if analysis_input.trend_regime == "bearish":
            reasons.append("当前更像单边下跌环境，盘中小 V 先按日内 T 看，不直接当成波段修复。")
        risk_warnings = [
            "LLM 不可用，本轮不能给出完整事件、板块、夜盘和期权确认，只保留条件式观察。",
            f"若后续有效跌破 L1 参考位 {l1:.2f} 或尾盘继续走弱，默认不抢早、不加仓。",
            "高波动标的即使放宽价位区间，也要同步降低仓位；缺少二次握手前不转成重仓观点。",
        ]
        return GeminiSignal(
            symbol=analysis_input.symbol,
            timestamp_utc=analysis_input.timestamp_utc,
            analysis_level="json_only",
            bias=bias,  # type: ignore[arg-type]
            sentiment_score=score,
            confidence=0.45,
            setup_type="no_trade",
            visual_confirmation="not_applicable",
            should_alert=False,
            entry_zone=[analysis_input.last_price * 0.995, analysis_input.last_price * 1.005],
            stop_loss=analysis_input.last_price - 1.5 * (analysis_input.atr_14 or 0),
            take_profit=[
                analysis_input.last_price + 2.0 * (analysis_input.atr_14 or 0),
                analysis_input.last_price + 2.5 * (analysis_input.atr_14 or 0),
            ],
            risk_reward_ratio=0.0,
            reasons=reasons,
            risk_warnings=risk_warnings,
        )

    def review_multimodal(
        self,
        analysis_input: TechnicalAnalysisInput,
        preliminary_signal: GeminiSignal,
        image_path: str | Path,
    ) -> GeminiSignal:
        data = preliminary_signal.json_dict()
        data["analysis_level"] = "multimodal_review"
        data["visual_confirmation"] = "confirmed"
        return GeminiSignal.model_validate(data)
