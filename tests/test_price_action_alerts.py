from datetime import datetime, timezone

import pandas as pd

from gemini_stock.rules.price_action_alerts import (
    build_price_action_card,
    build_price_action_alerts,
    format_price_action_alert,
)
from gemini_stock.schemas import GeminiSignal


def _signal(**updates) -> GeminiSignal:
    data = {
        "symbol": "TSLL",
        "timestamp_utc": datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc),
        "analysis_level": "multimodal_review",
        "bias": "bullish",
        "sentiment_score": 6.4,
        "confidence": 0.7,
        "setup_type": "breakout",
        "visual_confirmation": "confirmed",
        "should_alert": True,
        "entry_zone": [12.10, 12.20],
        "stop_loss": 11.90,
        "take_profit": [12.60, 12.80],
        "risk_reward_ratio": 1.5,
        "reasons": ["Price is holding near the AI entry zone."],
        "risk_warnings": ["Fast premarket moves can reverse."],
    }
    data.update(updates)
    return GeminiSignal(**data)


def _one_minute_frame(price: float) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "timestamp": datetime(2026, 1, 5, 15, 1, tzinfo=timezone.utc),
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": 1000,
            }
        ]
    )


def test_price_action_alert_triggers_bullish_entry_zone():
    alerts = build_price_action_alerts(_signal(), _one_minute_frame(12.15), trading_date="2026-01-05")

    assert len(alerts) == 1
    alert = alerts[0]
    assert alert.action == "buy"
    assert alert.event_key == "price_action_p2:TSLL:buy:2026-01-05:12.10-12.20"
    text = format_price_action_alert(alert)
    assert text.splitlines()[0] == "【价格到位】TSLL｜买入区"
    assert "现价 12.15｜参考 12.10-12.20" in text
    assert "动作：分批试探，别追价" in text
    assert "等级：P2｜一般买入提醒" in text
    assert "买入原因：价格进入 AI 买入参考区；Price is holding near the AI entry zone。" in text


def test_price_action_alert_triggers_bullish_take_profit_zone():
    alerts = build_price_action_alerts(_signal(), _one_minute_frame(12.70), trading_date="2026-01-05")

    assert len(alerts) == 1
    alert = alerts[0]
    assert alert.action == "sell"
    assert alert.event_key == "price_action:TSLL:sell:2026-01-05:12.60-12.80"
    text = format_price_action_alert(alert)
    assert "【价格到位】TSLL｜止盈区" in text
    assert "现价 12.70｜参考 12.60-12.80" in text
    assert "动作：分批止盈，保留计划" in text
    assert "卖出原因：价格进入 AI 卖出参考区；Price is holding near the AI entry zone。" in text


def test_price_action_alert_still_tracks_actionable_non_alert_signals():
    alerts = build_price_action_alerts(
        _signal(should_alert=False),
        _one_minute_frame(12.15),
        trading_date="2026-01-05",
    )

    assert len(alerts) == 1
    assert alerts[0].action == "buy"


def test_price_action_alert_skips_non_actionable_tight_targets():
    alerts = build_price_action_alerts(
        _signal(
            entry_zone=[15.56, 15.58],
            stop_loss=15.55,
            take_profit=[15.59, 15.61],
            risk_reward_ratio=0.4,
            setup_type="breakout",
            should_alert=True,
        ),
        _one_minute_frame(15.57),
        trading_date="2026-01-05",
    )

    assert alerts == []


def test_price_action_alert_uses_long_only_labels_for_bearish_signal():
    signal = _signal(
        bias="bearish",
        setup_type="bearish_breakdown",
        entry_zone=[12.14, 12.16],
        take_profit=[12.09, 11.96],
        stop_loss=12.27,
    )

    alerts = build_price_action_alerts(signal, _one_minute_frame(12.15), trading_date="2026-01-05")

    assert len(alerts) == 1
    text = format_price_action_alert(alerts[0])
    assert "【价格到位】TSLL｜减仓区" in text
    assert "动作：先减风险，不追空" in text
    assert "减仓原因：价格进入 AI 卖出参考区；Price is holding near the AI entry zone。" in text


def test_price_action_alert_can_include_position_context():
    alert = build_price_action_alerts(_signal(), _one_minute_frame(12.70), trading_date="2026-01-05")[0]

    text = format_price_action_alert(alert, position_qty=100, average_cost=12.30)

    assert "持仓 100 股｜成本 12.30" in text


def test_price_action_alert_marks_high_priority_for_strong_bullish_entry():
    signal = _signal(sentiment_score=8.2, confidence=0.78, risk_reward_ratio=2.1)
    alert = build_price_action_alerts(signal, _one_minute_frame(12.15), trading_date="2026-01-05")[0]

    text = format_price_action_alert(alert)

    assert alert.level == "P1"
    assert alert.event_key == "price_action_p1:TSLL:buy:2026-01-05:12.10-12.20"
    assert text.splitlines()[0] == "【优先到价提醒】TSLL｜买入区"


def test_build_price_action_card_for_high_priority_alert():
    signal = _signal(sentiment_score=8.2, confidence=0.78, risk_reward_ratio=2.1)
    alert = build_price_action_alerts(signal, _one_minute_frame(12.15), trading_date="2026-01-05")[0]

    card = build_price_action_card(alert, position_qty=100, average_cost=12.30)

    assert card["header"]["template"] == "red"
    assert "P1 特别推荐买入" in card["header"]["title"]["content"]
    markdown = card["elements"][0]["content"]
    assert "等级" in markdown
    assert "现价" in markdown
    assert "参考区" in markdown
    assert "**买入原因**：价格进入 AI 买入参考区；Price is holding near the AI entry zone。" in markdown
    assert len(card["elements"]) == 1
