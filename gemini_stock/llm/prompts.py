from __future__ import annotations

import json

from gemini_stock.schemas import GeminiSignal, TechnicalAnalysisInput


SYSTEM_INSTRUCTIONS = """
You are an analysis component for a US stock monitoring assistant.
This system is for research and alerts only. It is not investment advice.
You must not decide or execute trades. Python has already calculated all technical indicators.
Do not recalculate or infer RSI, MACD, VWAP, ATR, EMA, support, or resistance.
Return only JSON that matches the provided schema.
"""


ZHAOGE_FRAMEWORK_RULES = [
    "结合赵哥交易框架输出条件式建议：回避 / 观察 / 试探 / 等二次握手 / 确认后小仓 / 失效。",
    "不要把第一低点 L1 当成最终低点；先记录 L1，重点等待二次握手，也就是回踩接近 L1 但没有有效跌破。",
    "必须在 reasons 或 risk_warnings 中体现失效条件、不追价区、仓位约束。",
    "对 TSLL、CONL 等高波动或杠杆/加密相关标的，放宽价格区间的同时降低仓位，不给无条件买入/卖出指令。",
    "如果价格已经快速远离 L1 且没有二次回踩，标记为不追，等待回踩或尾盘确认。",
    "输出 entry_zone、stop_loss、take_profit 时，只给价位区间 + 条件 + 风险控制。",
]


def build_json_only_prompt(analysis_input: TechnicalAnalysisInput) -> str:
    payload = {
        "analysis_level": "json_only",
        "technical_snapshot": analysis_input.json_dict(),
        "technical_events": analysis_input.technical_events,
        "news_summary": analysis_input.news_summary,
        "zhaoge_framework_rules": ZHAOGE_FRAMEWORK_RULES,
        "required_schema": GeminiSignal.model_json_schema(),
        "output_rules": [
            "analysis_level must be json_only.",
            "bias must be bullish, bearish, or neutral.",
            "sentiment_score must be between -10 and 10.",
            "confidence must be between 0 and 1.",
            "setup_type must be bullish_reversal, bearish_breakdown, breakout, breakdown, or no_trade.",
            "visual_confirmation must be not_applicable for json_only.",
            "Set should_alert based on your analysis, but final alerting is decided by deterministic rules.",
        ],
    }
    return SYSTEM_INSTRUCTIONS + "\n\nInput:\n" + json.dumps(payload, ensure_ascii=False, default=str)


def build_multimodal_review_prompt(
    analysis_input: TechnicalAnalysisInput,
    preliminary_signal: GeminiSignal,
) -> str:
    payload = {
        "analysis_level": "multimodal_review",
        "technical_snapshot": analysis_input.json_dict(),
        "technical_events": analysis_input.technical_events,
        "news_summary": analysis_input.news_summary,
        "preliminary_json_only_signal": preliminary_signal.json_dict(),
        "zhaoge_framework_rules": ZHAOGE_FRAMEWORK_RULES,
        "review_task": [
            "Use the simplified chart only to visually confirm whether price structure supports the Level 1 conclusion.",
            "Do not recalculate RSI, MACD, VWAP, ATR, EMA, support, or resistance from the image.",
            "Return visual_confirmation as confirmed or rejected.",
            "Keep analysis_level as multimodal_review.",
        ],
        "required_schema": GeminiSignal.model_json_schema(),
    }
    return SYSTEM_INSTRUCTIONS + "\n\nInput:\n" + json.dumps(payload, ensure_ascii=False, default=str)
