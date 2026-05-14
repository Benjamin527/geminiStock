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
    "先判断现在是哪种模式，再决定是否沿用昨天剧本；至少区分 存量震荡 / 单边下跌 / 事件驱动 / 情绪过热。",
    "不要把第一低点 L1 当成最终低点；先记录 L1，重点等待二次握手，也就是回踩接近 L1 但没有有效跌破。",
    "如果属于单边下跌，默认先等确认，不把盘中小 V 直接当成趋势反转；更像 急跌试探 -> 小V先减半 -> 夜盘/次日再判断。",
    "尾盘结构，尤其美东 3:30 pm 之后的走法，是核心过滤条件；如果尾盘继续走弱，早盘英雄抄底的胜率更低。",
    "遇到事件窗口时，要区分 预期阶段 / 真空期 / 落地日 / 落地后分配期，不把事件前平静误判为事件后安全。",
    "如果涉及被动减仓、节日减仓、基金调仓、减持、锁仓解禁或类似卖压，按多日时间窗处理，更重视缺口回补、低位回踩和分批重建。",
    "必须在 reasons 或 risk_warnings 中体现失效条件、不追价区、仓位约束。",
    "对 TSLL、CONL 等高波动或杠杆/加密相关标的，放宽价格区间的同时降低仓位，不给无条件买入/卖出指令。",
    "如果价格已经快速远离 L1 且没有二次回踩，标记为不追，等待回踩或尾盘确认。",
    "如果缺少大盘、板块、龙头、夜盘/盘前、事件或期权活跃度等关键上下文，要明确说明数据缺口，并切换成条件式框架。",
    "输出 entry_zone、stop_loss、take_profit 时，只给价位区间 + 条件 + 风险控制。",
]

LEVERAGED_ETF_HINTS = {
    "CONL",
    "SOXL",
    "SOXS",
    "TQQQ",
    "SQQQ",
    "TSLL",
    "TSLQ",
    "NVDL",
    "NVDQ",
    "BITX",
    "BITU",
}

CRYPTO_EQUITY_HINTS = {
    "COIN",
    "CONL",
    "MARA",
    "MSTR",
    "RIOT",
    "IREN",
    "CIFR",
    "CLSK",
    "BITF",
    "HUT",
}


def _instrument_profile(symbol: str) -> tuple[str, str]:
    normalized = symbol.upper()
    if normalized in LEVERAGED_ETF_HINTS:
        return "leveraged_etf", "high"
    if normalized in CRYPTO_EQUITY_HINTS:
        return "crypto_related_equity", "high"
    if normalized.endswith("Q") and len(normalized) <= 5:
        return "etf", "normal"
    return "stock", "normal"


def _regime_hint(analysis_input: TechnicalAnalysisInput) -> str:
    price = analysis_input.last_price
    ema20 = analysis_input.ema_20
    ema50 = analysis_input.ema_50
    events = set(analysis_input.technical_events)
    news_text = " ".join(analysis_input.news_summary).lower()
    has_event = bool(analysis_input.news_summary) and "no high relevance news" not in news_text
    price_below_trend = (
        ema20 is not None
        and ema50 is not None
        and price <= ema20 <= ema50
        and "price_below_vwap" in events
    )
    price_above_trend = (
        ema20 is not None
        and ema50 is not None
        and price >= ema20 >= ema50
        and "price_above_vwap" in events
    )
    if has_event:
        return "事件驱动"
    if analysis_input.volume_regime == "spike" and "price_near_resistance" in events and price_above_trend:
        return "情绪过热"
    if analysis_input.trend_regime == "bearish" and price_below_trend:
        return "单边下跌"
    return "存量震荡"


def _zhaoge_context(analysis_input: TechnicalAnalysisInput) -> dict[str, object]:
    instrument_type, volatility = _instrument_profile(analysis_input.symbol)
    recent = analysis_input.ohlcv_recent[-8:]
    l1 = min((bar.low for bar in recent), default=analysis_input.last_price)
    bounce_pct = round((analysis_input.last_price - l1) / l1 * 100, 2) if l1 > 0 else 0.0
    available_context = [
        "technical_events",
        "news_summary",
        "recent_15m_ohlcv",
    ]
    missing_context = [
        "sector_and_leader_confirmation",
        "tail_session_after_3_30pm_structure",
        "night_session_or_premarket_follow_through",
        "options_flow_and_iv",
    ]
    return {
        "instrument_type_hint": instrument_type,
        "volatility_profile_hint": volatility,
        "regime_hint": _regime_hint(analysis_input),
        "l1_candidate": round(l1, 4),
        "bounce_distance_from_l1_pct": bounce_pct,
        "available_context": available_context,
        "missing_context_to_acknowledge_if_needed": missing_context,
        "tail_session_rule": "If the current setup depends on stabilization, weigh US 3:30 pm onward structure more heavily than midday noise.",
        "event_window_rule": "If news or catalyst risk is present, separate pre-event calm, event day, and post-event distribution instead of treating them as one tape.",
    }


def build_json_only_prompt(analysis_input: TechnicalAnalysisInput) -> str:
    payload = {
        "analysis_level": "json_only",
        "technical_snapshot": analysis_input.json_dict(),
        "technical_events": analysis_input.technical_events,
        "news_summary": analysis_input.news_summary,
        "zhaoge_context": _zhaoge_context(analysis_input),
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
            "Use reasons and risk_warnings to state the regime/mode, invalidation, no-chase logic, and position constraint explicitly.",
            "When context is missing, say what is missing instead of pretending the setup is fully confirmed.",
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
        "zhaoge_context": _zhaoge_context(analysis_input),
        "zhaoge_framework_rules": ZHAOGE_FRAMEWORK_RULES,
        "review_task": [
            "Use the simplified chart only to visually confirm whether price structure supports the Level 1 conclusion.",
            "Do not recalculate RSI, MACD, VWAP, ATR, EMA, support, or resistance from the image.",
            "Return visual_confirmation as confirmed or rejected.",
            "Keep analysis_level as multimodal_review.",
            "If the structure still looks one-way down or late-session weak, be stricter and prefer waiting for confirmation over early dip-buying.",
        ],
        "required_schema": GeminiSignal.model_json_schema(),
    }
    return SYSTEM_INSTRUCTIONS + "\n\nInput:\n" + json.dumps(payload, ensure_ascii=False, default=str)
