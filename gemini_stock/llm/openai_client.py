from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Any

import requests
from pydantic import ValidationError

from gemini_stock.llm.gemini_client import GeminiAnalysisError
from gemini_stock.llm.prompts import build_json_only_prompt, build_multimodal_review_prompt
from gemini_stock.schemas import GeminiSignal, TechnicalAnalysisInput

logger = logging.getLogger(__name__)


class OpenAICompatibleAnalyzer:
    def __init__(self, api_key: str | None, base_url: str, model: str) -> None:
        if not api_key:
            raise GeminiAnalysisError("OPENAI_API_KEY is required when OpenAI-compatible analysis is enabled")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model

    def analyze_json_only(self, analysis_input: TechnicalAnalysisInput) -> GeminiSignal:
        prompt = build_json_only_prompt(analysis_input)
        messages = [
            {"role": "user", "content": prompt},
        ]
        return self._generate_signal(messages, analysis_input.symbol)

    def review_multimodal(
        self,
        analysis_input: TechnicalAnalysisInput,
        preliminary_signal: GeminiSignal,
        image_path: str | Path,
    ) -> GeminiSignal:
        prompt = build_multimodal_review_prompt(analysis_input, preliminary_signal)
        image = Path(image_path)
        image_data = base64.b64encode(image.read_bytes()).decode("ascii")
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_data}"}},
                ],
            },
        ]
        return self._generate_signal(messages, analysis_input.symbol)

    def _generate_signal(self, messages: list[dict[str, Any]], symbol: str) -> GeminiSignal:
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                response = requests.post(
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.model,
                        "messages": messages,
                        "temperature": 0.2,
                        "response_format": {"type": "json_object"},
                    },
                    timeout=60,
                )
                response.raise_for_status()
                content = response.json()["choices"][0]["message"]["content"]
                payload = content if isinstance(content, dict) else json.loads(content or "{}")
                return GeminiSignal.model_validate(payload)
            except (KeyError, IndexError, TypeError, json.JSONDecodeError, ValidationError, requests.RequestException) as exc:
                last_error = exc
                logger.warning(
                    "openai_compatible_analysis_attempt_failed",
                    extra={"symbol": symbol, "attempt": attempt + 1, "error": str(exc)},
                )
        raise GeminiAnalysisError(f"OpenAI-compatible API returned invalid signal after retry: {last_error}")
