from datetime import datetime, timezone

from gemini_stock.notify.channels import (
    FeishuNotifier,
    format_alert,
    send_feishu_text,
    send_feishu_interactive_card,
)
from gemini_stock.schemas import AlertDecision, GeminiSignal, TechnicalSnapshot


def _decision() -> AlertDecision:
    snapshot = TechnicalSnapshot(
        symbol="SPY",
        interval="15m",
        timestamp_utc=datetime.now(timezone.utc),
        open=99,
        high=101,
        low=98,
        close=100,
        volume=1000,
        rsi_14=30,
        macd=1,
        macd_signal=0.5,
        macd_histogram=0.5,
        ema_20=100,
        ema_50=99,
        vwap=99.5,
        atr_14=2,
        recent_4h_high=103,
        recent_4h_low=97,
        support_levels=[97, 98],
        resistance_levels=[102, 103],
    )
    signal = GeminiSignal(
        symbol="SPY",
        timestamp_utc=datetime.now(timezone.utc),
        analysis_level="multimodal_review",
        bias="bullish",
        sentiment_score=8,
        confidence=0.8,
        setup_type="bullish_reversal",
        visual_confirmation="confirmed",
        should_alert=True,
        entry_zone=[99, 100],
        stop_loss=96,
        take_profit=[105, 106],
        risk_reward_ratio=2,
        reasons=["Oversold bounce setup."],
        risk_warnings=["Market breadth may weaken."],
    )
    return AlertDecision(
        symbol="SPY",
        should_alert=True,
        reason="buy_alert",
        timestamp_utc=datetime.now(timezone.utc),
        signal=signal,
        technical_snapshot=snapshot,
    )


def _bearish_decision() -> AlertDecision:
    bullish = _decision()
    signal = bullish.signal.model_copy(
        update={
            "symbol": "TSLL",
            "bias": "bearish",
            "setup_type": "bearish_breakdown",
            "entry_zone": [12.14, 12.16],
            "stop_loss": 12.27,
            "take_profit": [12.09, 11.96],
            "risk_reward_ratio": 1.69,
            "sentiment_score": -5.0,
            "confidence": 0.7,
        }
    )
    snapshot = bullish.technical_snapshot.model_copy(update={"symbol": "TSLL", "close": 12.15, "atr_14": 0.13, "ema_50": 12.24, "rsi_14": 50.6})
    return bullish.model_copy(update={"symbol": "TSLL", "signal": signal, "technical_snapshot": snapshot})


def test_feishu_notifier_returns_false_without_webhook():
    assert FeishuNotifier(None).send(_decision()) is False


def test_feishu_notifier_suppresses_messages_after_23_beijing():
    notifier = FeishuNotifier(
        "https://open.feishu.cn/open-apis/bot/v2/hook/token",
        now_fn=lambda: datetime(2026, 1, 5, 15, 1, tzinfo=timezone.utc),
    )

    assert notifier.send(_decision()) is False


def test_feishu_notifier_suppresses_messages_after_midnight_beijing():
    notifier = FeishuNotifier(
        "https://open.feishu.cn/open-apis/bot/v2/hook/token",
        now_fn=lambda: datetime(2026, 1, 5, 17, 1, tzinfo=timezone.utc),
    )

    assert notifier.send(_decision()) is False


def test_feishu_notifier_posts_text_payload(monkeypatch):
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

    def fake_post(url, json, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr("gemini_stock.notify.channels.requests.post", fake_post)

    notifier = FeishuNotifier(
        "https://open.feishu.cn/open-apis/bot/v2/hook/token",
        now_fn=lambda: datetime(2026, 1, 5, 4, 0, tzinfo=timezone.utc),
    )

    assert notifier.send(_decision()) is True
    assert captured["url"].endswith("/token")
    assert captured["json"]["msg_type"] == "interactive"
    assert "SPY" in captured["json"]["card"]["header"]["title"]["content"]
    assert "SPY" in captured["json"]["card"]["elements"][0]["content"]
    assert captured["timeout"] == 10


def test_send_feishu_text_ignores_mention_config(monkeypatch):
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

    def fake_post(url, json, timeout):
        captured["json"] = json
        return Response()

    monkeypatch.setattr("gemini_stock.notify.channels.requests.post", fake_post)

    ok = send_feishu_text(
        "https://open.feishu.cn/open-apis/bot/v2/hook/token",
        "分析一下$asts",
        now_fn=lambda: datetime(2026, 1, 5, 4, 0, tzinfo=timezone.utc),
        mention_open_id="ou_36013872f283455140c3f746097896df",
        mention_name="交易分析",
    )

    assert ok is True
    assert captured["json"]["msg_type"] == "interactive"
    assert captured["json"]["card"]["header"]["title"]["content"] == "分析一下$asts"
    assert captured["json"]["card"]["elements"][0]["content"] == "分析一下$asts"


def test_send_feishu_interactive_card_posts_card_payload(monkeypatch):
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

    def fake_post(url, json, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr("gemini_stock.notify.channels.requests.post", fake_post)

    ok = send_feishu_interactive_card(
        "https://open.feishu.cn/open-apis/bot/v2/hook/token",
        {"header": {"title": {"content": "x"}}},
        now_fn=lambda: datetime(2026, 1, 5, 4, 0, tzinfo=timezone.utc),
        mention_open_id="ou_36013872f283455140c3f746097896df",
        mention_name="交易分析",
    )

    assert ok is True
    assert captured["json"]["msg_type"] == "interactive"
    assert "card" in captured["json"]
    assert captured["json"]["card"] == {"header": {"title": {"content": "x"}}}
    assert captured["timeout"] == 10


def test_alert_text_uses_chinese_labels_and_translated_enums():
    text = format_alert(_decision())

    assert text.splitlines()[0] == "【盯盘提醒】SPY｜看多｜看多反转"
    assert "现价 100.00｜RSI 30.0｜置信 80%" in text
    assert "买入 99.00-100.00｜止损 96.00｜目标 105.00-106.00" in text
    assert "动作：分批试探，等二次确认" in text
    assert "提示：仅研究提醒，破失效位先降风险。" in text
    assert "买入原因：Oversold bounce setup。" in text
    assert len(text.splitlines()) <= 7
    assert "Bias:" not in text
    assert "Setup:" not in text


def test_alert_text_uses_long_only_labels_for_bearish_signal():
    text = format_alert(_bearish_decision())

    assert text.splitlines()[0] == "【盯盘提醒】TSLL｜看空｜看空破位"
    assert "卖出 12.14-12.16｜风险 12.27｜回补 11.96-12.09" in text
    assert "动作：先减风险，不追空" in text
    assert "卖出原因：Oversold bounce setup。" in text
    assert len(text.splitlines()) <= 7

