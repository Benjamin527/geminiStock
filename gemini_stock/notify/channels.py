from __future__ import annotations

import logging
from datetime import datetime, time
from typing import Protocol
from zoneinfo import ZoneInfo

import requests

from gemini_stock.schemas import AlertDecision

logger = logging.getLogger(__name__)
BEIJING = ZoneInfo("Asia/Shanghai")


class Notifier(Protocol):
    channel: str

    def send(self, decision: AlertDecision) -> bool:
        ...


def _format_band(values: list[float], bias: str) -> str:
    if len(values) < 2:
        return "-"
    ordered = sorted(float(value) for value in values[:2])
    if bias == "bearish":
        return f"{ordered[0]:.2f}-{ordered[1]:.2f}"
    return f"{ordered[0]:.2f}-{ordered[1]:.2f}"


def _trade_labels(bias: str) -> tuple[str, str, str]:
    if bias == "bearish":
        return ("卖出参考", "风险位", "回补买入")
    return ("买入参考", "止损", "卖出参考")


def _action_text(bias: str) -> str:
    if bias == "bearish":
        return "先减风险，不追空"
    if bias == "bullish":
        return "分批试探，等二次确认"
    return "先观察，等二次握手"


def _reason_label(bias: str) -> str:
    if bias == "bearish":
        return "卖出原因"
    if bias == "bullish":
        return "买入原因"
    return "观察原因"


def _reason_text(reasons: list[str], fallback: str) -> str:
    cleaned = [item.strip().rstrip("。.;； ") for item in reasons if item and item.strip()]
    if not cleaned:
        return fallback
    return "；".join(cleaned[:2])


def format_alert(decision: AlertDecision) -> str:
    signal = decision.signal
    technical = decision.technical_snapshot
    bias_label = {
        "bullish": "看多",
        "bearish": "看空",
        "neutral": "中性",
    }.get(signal.bias, signal.bias)
    setup_label = {
        "bullish_reversal": "看多反转",
        "bearish_breakdown": "看空破位",
        "breakout": "突破",
        "no_trade": "观望",
    }.get(signal.setup_type, signal.setup_type)
    entry_label, stop_label, target_label = _trade_labels(signal.bias)
    target_short = "回补" if signal.bias == "bearish" else "目标"
    stop_short = "风险" if signal.bias == "bearish" else stop_label
    reason_label = _reason_label(signal.bias)
    reason_text = _reason_text(signal.reasons, f"当前结构满足 {setup_label} 条件，但仍要先看失效位与仓位约束")
    return "\n".join(
        [
            f"【盯盘提醒】{signal.symbol}｜{bias_label}｜{setup_label}",
            "----------------",
            f"现价 {technical.close:.2f}｜RSI {_fmt_one_or_dash(technical.rsi_14)}｜置信 {signal.confidence:.0%}",
            f"{entry_label.replace('参考', '')} {_format_band(signal.entry_zone, signal.bias)}｜{stop_short} {signal.stop_loss:.2f}｜{target_short} {_format_band(signal.take_profit, signal.bias)}",
            f"动作：{_action_text(signal.bias)}",
            "提示：仅研究提醒，破失效位先降风险。",
            f"{reason_label}：{reason_text}。",
        ]
    )


def format_opening_silence_self_check(
    current_time_label: str,
    worker_status: str,
    market_data_status: str,
) -> str:
    return "\n".join(
        [
            "【运行自检】开盘静默 30 分钟",
            "----------------",
            f"时间：{current_time_label}",
            "结论：发现异常，近 30 分钟没有任何推送",
            f"Worker：{worker_status}",
            f"行情：{market_data_status}",
            "提示：请检查行情源、容器运行状态和通知链路。",
        ]
    )


def build_opening_silence_self_check_card(
    current_time_label: str,
    worker_status: str,
    market_data_status: str,
) -> dict:
    return {
        "config": {"wide_screen_mode": True, "enable_forward": True},
        "header": {
            "template": "orange",
            "title": {"tag": "plain_text", "content": "运行自检｜开盘静默 30 分钟"},
        },
        "elements": [
            {
                "tag": "markdown",
                "content": (
                    f"**时间**：{current_time_label}\n"
                    f"**结论**：发现异常，近 30 分钟没有任何推送\n"
                    f"**Worker**：{worker_status}\n"
                    f"**行情**：{market_data_status}\n"
                    "**提示**：请检查行情源、容器运行状态和通知链路。"
                ),
            }
        ],
    }


def send_feishu_text(
    webhook_url: str | None,
    text: str,
    now_fn=None,
    bypass_quiet_hours: bool = False,
    mention_open_id: str | None = None,
    mention_name: str = "",
) -> bool:
    if not webhook_url:
        logger.info("feishu_not_configured")
        return False
    now_fn = now_fn or (lambda: datetime.now(BEIJING))
    current = now_fn().astimezone(BEIJING)
    if not bypass_quiet_hours and (current.time() >= time(23, 0) or current.time() < time(9, 0)):
        logger.info("feishu_quiet_hours_active", extra={"beijing_time": current.isoformat()})
        return False
    response = requests.post(
        webhook_url,
        json={"msg_type": "interactive", "card": _build_text_card(text)},
        timeout=10,
    )
    response.raise_for_status()
    return True


def send_feishu_interactive_card(
    webhook_url: str | None,
    card: dict,
    now_fn=None,
    bypass_quiet_hours: bool = False,
    mention_open_id: str | None = None,
    mention_name: str = "",
) -> bool:
    if not webhook_url:
        logger.info("feishu_not_configured")
        return False
    now_fn = now_fn or (lambda: datetime.now(BEIJING))
    current = now_fn().astimezone(BEIJING)
    if not bypass_quiet_hours and (current.time() >= time(23, 0) or current.time() < time(9, 0)):
        logger.info("feishu_quiet_hours_active", extra={"beijing_time": current.isoformat()})
        return False
    response = requests.post(
        webhook_url,
        json={"msg_type": "interactive", "card": card},
        timeout=10,
    )
    response.raise_for_status()
    return True


class TelegramNotifier:
    channel = "telegram"

    def __init__(self, bot_token: str | None, chat_id: str | None) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id

    def send(self, decision: AlertDecision) -> bool:
        if not self.bot_token or not self.chat_id:
            logger.info("telegram_not_configured", extra={"symbol": decision.symbol})
            return False
        response = requests.post(
            f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
            json={"chat_id": self.chat_id, "text": format_alert(decision)},
            timeout=10,
        )
        response.raise_for_status()
        return True


class FeishuNotifier:
    channel = "feishu"

    def __init__(
        self,
        webhook_url: str | None,
        now_fn=None,
        mention_open_id: str | None = None,
        mention_name: str = "",
    ) -> None:
        self.webhook_url = webhook_url
        self.now_fn = now_fn or (lambda: datetime.now(BEIJING))
        self.mention_open_id = mention_open_id
        self.mention_name = mention_name

    def send(self, decision: AlertDecision) -> bool:
        return send_feishu_text(
            self.webhook_url,
            format_alert(decision),
            now_fn=self.now_fn,
            mention_open_id=self.mention_open_id,
            mention_name=self.mention_name,
        )


class WeComNotifier:
    channel = "wecom"

    def __init__(self, webhook_url: str | None) -> None:
        self.webhook_url = webhook_url

    def send(self, decision: AlertDecision) -> bool:
        if not self.webhook_url:
            logger.info("wecom_not_configured", extra={"symbol": decision.symbol})
            return False
        response = requests.post(
            self.webhook_url,
            json={"msgtype": "text", "text": {"content": format_alert(decision)}},
            timeout=10,
        )
        response.raise_for_status()
        return True


def _fmt_or_dash(value: float | None) -> str:
    return f"{value:.2f}" if value is not None else "-"


def _fmt_one_or_dash(value: float | None) -> str:
    return f"{value:.1f}" if value is not None else "-"


def _build_text_card(text: str) -> dict:
    lines = [line for line in text.splitlines() if line.strip()]
    title = lines[0][:80] if lines else "盯盘提醒"
    return {
        "config": {"wide_screen_mode": True, "enable_forward": True},
        "header": {
            "template": "blue",
            "title": {"tag": "plain_text", "content": title},
        },
        "elements": [
            {
                "tag": "markdown",
                "content": text,
            }
        ],
    }
