from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import date, datetime, time as dt_time, timedelta, timezone

import pandas as pd

from gemini_stock.benchmarks import BenchmarkForecast, build_benchmark_forecast
from gemini_stock.charts.renderer import ChartRenderer
from gemini_stock.config import Settings, load_settings
from gemini_stock.data.base import MarketDataError, NEW_YORK
from gemini_stock.data.provider_factory import create_market_data_provider
from gemini_stock.data.yfinance_provider import YFinanceMarketDataProvider, candles_from_frame
from gemini_stock.features.analysis_input import build_analysis_input, build_news_summary, is_strong_candidate
from gemini_stock.features.events import build_technical_events
from gemini_stock.features.snapshot import build_technical_snapshot
from gemini_stock.llm.gemini_client import GeminiAnalysisError, GeminiAnalyzer, RuleBasedFallbackAnalyzer
from gemini_stock.llm.openai_client import OpenAICompatibleAnalyzer
from gemini_stock.logging_config import configure_logging
from gemini_stock.news.base import NullNewsProvider
from gemini_stock.notify.channels import (
    BEIJING,
    FeishuNotifier,
    TelegramNotifier,
    WeComNotifier,
    format_premarket_brief,
    send_feishu_text,
)
from gemini_stock.replay import build_daily_review, format_daily_review
from gemini_stock.rules.alert_rules import AlertRuleEngine, BenchmarkAlertRuleEngine
from gemini_stock.rules.price_action_alerts import build_price_action_alerts, format_price_action_alert
from gemini_stock.schedule import MarketSession, get_schedule_decision
from gemini_stock.storage.db import Database
from gemini_stock.storage.mysql_sync import MySQLSync, MySQLSyncConfig

logger = logging.getLogger(__name__)


@dataclass
class SymbolRunResult:
    symbol: str
    profile: str
    snapshot: object
    signal: object
    candles_1m: pd.DataFrame


@dataclass
class RuntimeContext:
    data_provider: object
    news_provider: object
    renderer: ChartRenderer
    analyzer: object
    fallback_analyzer: RuleBasedFallbackAnalyzer
    notifiers: list[object]

    @classmethod
    def from_settings(cls, settings: Settings) -> RuntimeContext:
        data_provider = (
            YFinanceMarketDataProvider(settings.delayed_data_tolerance_minutes)
            if settings.data_provider == "yfinance"
            else create_market_data_provider(settings)
        )
        return cls(
            data_provider=data_provider,
            news_provider=NullNewsProvider(),
            renderer=ChartRenderer(settings.chart_dir),
            analyzer=create_analyzer(settings),
            fallback_analyzer=RuleBasedFallbackAnalyzer(),
            notifiers=[
                TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id),
                FeishuNotifier(settings.feishu_webhook_url),
                WeComNotifier(settings.wecom_webhook_url),
            ],
        )


def create_analyzer(settings: Settings):
    if settings.llm_provider == "openai":
        return OpenAICompatibleAnalyzer(settings.openai_api_key, settings.openai_base_url, settings.openai_model)
    if settings.llm_provider == "gemini":
        return GeminiAnalyzer(settings.gemini_api_key, settings.gemini_model)
    if settings.llm_provider == "fallback":
        return RuleBasedFallbackAnalyzer()
    if settings.openai_api_key:
        return OpenAICompatibleAnalyzer(settings.openai_api_key, settings.openai_base_url, settings.openai_model)
    if settings.gemini_api_key:
        return GeminiAnalyzer(settings.gemini_api_key, settings.gemini_model)
    return RuleBasedFallbackAnalyzer()


def analyze_json_only_with_fallback(
    analyzer,
    fallback_analyzer: RuleBasedFallbackAnalyzer,
    analysis_input,
    settings: Settings,
):
    try:
        return analyzer.analyze_json_only(analysis_input), None
    except GeminiAnalysisError as exc:
        if not settings.llm_fallback_on_error:
            raise
        fallback_signal = fallback_analyzer.analyze_json_only(analysis_input)
        return fallback_signal, str(exc)


def run_symbol(
    symbol: str,
    settings: Settings,
    db: Database,
    rules: AlertRuleEngine | BenchmarkAlertRuleEngine,
    profile: str = "primary",
    context: RuntimeContext | None = None,
) -> SymbolRunResult | None:
    runtime = context or RuntimeContext.from_settings(settings)

    logger.info("symbol_run_started", extra={"symbol": symbol})
    candles_1m = runtime.data_provider.get_ohlcv(symbol, "1m", settings.yfinance_period_1m)
    candles_15m = runtime.data_provider.get_ohlcv(symbol, "15m", settings.yfinance_period_15m)
    db.save_candles(candles_from_frame(symbol, "1m", candles_1m))
    db.save_candles(candles_from_frame(symbol, "15m", candles_15m))

    snapshot = build_technical_snapshot(symbol, candles_15m)
    db.save_feature(snapshot)
    existing_signal = db.get_successful_signal_for_snapshot(symbol, snapshot.timestamp_utc)
    if existing_signal is not None:
        logger.info("llm_analysis_skipped_same_snapshot", extra={"symbol": symbol, "snapshot_timestamp": snapshot.timestamp_utc.isoformat()})
        result = SymbolRunResult(symbol=symbol, profile=profile, snapshot=snapshot, signal=existing_signal, candles_1m=candles_1m)
        if profile == "primary":
            maybe_send_price_action_alerts(settings, db, symbol, existing_signal, candles_1m)
        return result

    news = runtime.news_provider.get_news([symbol])
    db.save_news(news)
    news_summary = build_news_summary(news)
    technical_events = build_technical_events(candles_15m, snapshot)
    analysis_input = build_analysis_input(symbol, candles_15m, snapshot, technical_events, news_summary)

    level1_input = {
        "analysis_level": "json_only",
        "has_image": False,
        "symbol_profile": profile,
        "symbol": symbol,
        "technical_snapshot": analysis_input.json_dict(),
        "technical_events": technical_events,
        "news_summary": news_summary,
    }
    try:
        level1_signal, fallback_error = analyze_json_only_with_fallback(
            runtime.analyzer,
            runtime.fallback_analyzer,
            analysis_input,
            settings,
        )
        if fallback_error:
            level1_input["fallback_reason"] = fallback_error
            level1_input["fallback_used"] = True
        db.save_llm_output(symbol, level1_input, level1_signal.json_dict(), None)
    except GeminiAnalysisError as exc:
        db.save_llm_output(symbol, level1_input, None, str(exc))
        logger.error("gemini_analysis_failed", extra={"symbol": symbol, "error": str(exc)})
        return None

    final_signal = level1_signal
    if profile == "primary" and is_strong_candidate(snapshot, analysis_input, technical_events, news, level1_signal):
        image_path = runtime.renderer.render_simplified(symbol, candles_15m, snapshot)
        level2_input = {
            "analysis_level": "multimodal_review",
            "has_image": True,
            "symbol_profile": profile,
            "symbol": symbol,
            "technical_snapshot": analysis_input.json_dict(),
            "technical_events": technical_events,
            "news_summary": news_summary,
            "preliminary_signal": level1_signal.json_dict(),
            "simplified_chart_image_path": str(image_path),
        }
        try:
            final_signal = runtime.analyzer.review_multimodal(analysis_input, level1_signal, image_path)
            db.save_llm_output(symbol, level2_input, final_signal.json_dict(), None)
        except GeminiAnalysisError as exc:
            db.save_llm_output(symbol, level2_input, None, str(exc))
            logger.error("gemini_multimodal_review_failed", extra={"symbol": symbol, "error": str(exc)})
            return None

    if isinstance(rules, BenchmarkAlertRuleEngine):
        decision = rules.evaluate(final_signal, snapshot, candles_1m)
    else:
        decision = rules.evaluate(final_signal, snapshot)
    result = SymbolRunResult(symbol=symbol, profile=profile, snapshot=snapshot, signal=final_signal, candles_1m=candles_1m)
    if profile == "primary" and (decision.should_alert or decision.reason == "cooldown_active"):
        maybe_send_price_action_alerts(settings, db, symbol, final_signal, candles_1m)
    if not decision.should_alert:
        logger.info("alert_skipped", extra={"symbol": symbol, "reason": decision.reason})
        return result

    latest_timestamp = pd.Timestamp(candles_1m.sort_values("timestamp").iloc[-1]["timestamp"]).to_pydatetime()
    trading_date = latest_timestamp.astimezone(NEW_YORK).date().isoformat()
    event_key = build_decision_alert_event_key(decision, trading_date)
    payload = decision.json_dict()
    payload["event_key"] = event_key
    payload["type"] = "benchmark_alert" if isinstance(rules, BenchmarkAlertRuleEngine) else "primary_alert"
    for notifier in runtime.notifiers:
        try:
            if not should_send_decision_alert(db, notifier.channel, decision, event_key, datetime.now(timezone.utc), settings.alert_cooldown_minutes):
                logger.info("alert_skipped", extra={"symbol": symbol, "reason": "persistent_cooldown_or_duplicate", "channel": notifier.channel})
                continue
            sent = notifier.send(decision)
            if sent:
                db.save_alert(symbol, payload, notifier.channel)
        except Exception as exc:
            logger.error("notification_failed", extra={"symbol": symbol, "channel": notifier.channel, "error": str(exc)})

    return result


def run_once(settings: Settings) -> None:
    db = Database(settings.database_path)
    db.initialize()
    context = RuntimeContext.from_settings(settings)
    primary_rules = AlertRuleEngine(settings.alert_cooldown_minutes)
    benchmark_rules = BenchmarkAlertRuleEngine(settings.alert_cooldown_minutes)
    benchmark_results: list[SymbolRunResult] = []
    for symbol in settings.symbols:
        try:
            run_symbol(symbol, settings, db, primary_rules, profile="primary", context=context)
        except MarketDataError as exc:
            db.save_llm_output(symbol, {"analysis_level": "market_data", "symbol": symbol}, None, str(exc))
            logger.error("market_data_failed", extra={"symbol": symbol, "error": str(exc)})
        except Exception as exc:
            logger.exception("symbol_run_failed", extra={"symbol": symbol, "error": str(exc)})
    for symbol in settings.benchmark_symbols:
        try:
            result = run_symbol(symbol, settings, db, benchmark_rules, profile="benchmark", context=context)
            if result is not None:
                benchmark_results.append(result)
        except MarketDataError as exc:
            db.save_llm_output(symbol, {"analysis_level": "market_data", "symbol": symbol}, None, str(exc))
            logger.error("market_data_failed", extra={"symbol": symbol, "error": str(exc)})
        except Exception as exc:
            logger.exception("symbol_run_failed", extra={"symbol": symbol, "error": str(exc)})
    maybe_send_premarket_brief(settings, db, benchmark_results)
    run_maintenance_tasks(settings, db=db)
    maybe_sync_remote_mysql(settings, db)


def run_maintenance_tasks(
    settings: Settings,
    db: Database | None = None,
    now: datetime | None = None,
) -> None:
    database = db or Database(settings.database_path)
    if db is None:
        database.initialize()
    maybe_send_daily_review(settings, database, now=now)


def maybe_send_premarket_brief(
    settings: Settings,
    db: Database,
    benchmark_results: list[SymbolRunResult],
    now: datetime | None = None,
) -> None:
    if not benchmark_results:
        return
    current = (now or datetime.now(NEW_YORK)).astimezone(NEW_YORK)
    schedule = get_schedule_decision(current)
    if schedule.session != MarketSession.PREMARKET:
        return
    if current.time() < dt_time(8, 30):
        return
    event_key = f"premarket_briefing:{current.date().isoformat()}"
    if db.has_alert_event("feishu", event_key):
        return

    forecasts = [
        build_benchmark_forecast(result.snapshot, result.signal)
        for result in benchmark_results
        if result.profile == "benchmark"
    ]
    if not forecasts:
        return
    text = format_premarket_brief(forecasts, trading_date=current.date().isoformat())
    try:
        sent = send_feishu_text(settings.feishu_webhook_url, text)
    except Exception as exc:
        logger.error("premarket_brief_failed", extra={"error": str(exc)})
        return
    if sent:
        db.save_alert(
            "MARKET_BRIEF",
            {
                "event_key": event_key,
                "type": "premarket_briefing",
                "symbols": [forecast.symbol for forecast in forecasts],
                "text": text,
            },
            "feishu",
        )


def maybe_send_daily_review(
    settings: Settings,
    db: Database,
    now: datetime | None = None,
) -> None:
    current = (now or datetime.now(BEIJING)).astimezone(BEIJING)
    if current.time() < dt_time(8, 0):
        return
    trading_date = _previous_calendar_date(current.date())
    event_key = f"daily_review:{trading_date.isoformat()}"
    if db.has_alert_event("feishu", event_key):
        return
    review = build_daily_review(db, settings.symbols, trading_date)
    if review.evaluated_count == 0:
        return
    text = format_daily_review(review)
    try:
        sent = send_feishu_text(settings.feishu_webhook_url, text, bypass_quiet_hours=True)
    except Exception as exc:
        logger.error("daily_review_failed", extra={"error": str(exc), "trading_date": trading_date.isoformat()})
        return
    if sent:
        db.save_alert(
            "DAILY_REVIEW",
            {
                "event_key": event_key,
                "type": "daily_review",
                "symbols": settings.symbols,
                "trading_date": trading_date.isoformat(),
                "accuracy_pct": review.accuracy_pct,
                "text": text,
            },
            "feishu",
        )


def maybe_send_price_action_alerts(
    settings: Settings,
    db: Database,
    symbol: str,
    signal: object,
    candles_1m: pd.DataFrame,
    trading_date: str | None = None,
) -> int:
    if symbol not in settings.symbols:
        return 0
    if candles_1m.empty:
        return 0
    latest_timestamp = pd.Timestamp(candles_1m.sort_values("timestamp").iloc[-1]["timestamp"]).to_pydatetime()
    alert_date = trading_date or latest_timestamp.astimezone(NEW_YORK).date().isoformat()
    alerts = build_price_action_alerts(signal, candles_1m, trading_date=alert_date)  # type: ignore[arg-type]
    sent_count = 0
    for alert in alerts:
        if db.has_alert_event("feishu", alert.event_key):
            continue
        text = format_price_action_alert(
            alert,
            position_qty=settings.positions.get(symbol),
            average_cost=settings.average_costs.get(symbol),
        )
        try:
            sent = send_feishu_text(settings.feishu_webhook_url, text)
        except Exception as exc:
            logger.error("price_action_alert_failed", extra={"symbol": symbol, "event_key": alert.event_key, "error": str(exc)})
            continue
        if sent:
            db.save_alert(
                symbol,
                {
                    "event_key": alert.event_key,
                    "type": "price_action",
                    "symbol": alert.symbol,
                    "action": alert.action,
                    "latest_price": alert.latest_price,
                    "reference_label": alert.reference_label,
                    "reference_zone": alert.reference_zone,
                    "text": text,
                },
                "feishu",
            )
            sent_count += 1
    return sent_count


def build_decision_alert_event_key(decision, trading_date: str) -> str:
    signal = decision.signal
    return ":".join(
        [
            "alert",
            decision.symbol,
            decision.reason,
            trading_date,
            _event_zone(signal.entry_zone),
            _event_zone(signal.take_profit),
        ]
    )


def should_send_decision_alert(
    db: Database,
    channel: str,
    decision,
    event_key: str,
    now: datetime,
    cooldown_minutes: int,
) -> bool:
    if db.has_alert_event(channel, event_key):
        return False
    alert_type = "benchmark_alert" if str(decision.reason).startswith("benchmark_") else "primary_alert"
    return not db.has_recent_alert_type(decision.symbol, channel, alert_type, now=now, within_minutes=cooldown_minutes)


def _event_zone(values: list[float]) -> str:
    if len(values) < 2:
        return "-"
    low, high = sorted(float(value) for value in values[:2])
    return f"{low:.2f}-{high:.2f}"


def _previous_calendar_date(current: date) -> date:
    return current - timedelta(days=1)


def maybe_sync_remote_mysql(settings: Settings, db: Database) -> None:
    if not settings.sync_remote_mysql:
        return
    if not all([settings.remote_mysql_host, settings.remote_mysql_user, settings.remote_mysql_password]):
        logger.warning("remote_mysql_sync_skipped_missing_config")
        return
    syncer = MySQLSync(
        MySQLSyncConfig(
            host=settings.remote_mysql_host,
            port=settings.remote_mysql_port,
            user=settings.remote_mysql_user,
            password=settings.remote_mysql_password,
            database=settings.remote_mysql_database,
        )
    )
    try:
        inserted = syncer.sync_from_sqlite(db.path)
        logger.info("remote_mysql_sync_completed", extra={"inserted": inserted, "database": settings.remote_mysql_database})
    except Exception as exc:
        logger.error("remote_mysql_sync_failed", extra={"error": str(exc), "database": settings.remote_mysql_database})


def main() -> None:
    configure_logging()
    settings = load_settings()
    logger.info(
        "service_started",
        extra={
            "symbols": settings.symbols,
            "benchmark_symbols": settings.benchmark_symbols,
            "llm_provider": settings.llm_provider,
            "model": settings.openai_model if settings.llm_provider == "openai" else settings.gemini_model,
        },
    )
    while True:
        schedule = get_schedule_decision()
        if schedule.should_run:
            logger.info("scheduled_run_started", extra={"session": schedule.session, "interval_seconds": schedule.interval_seconds})
            run_once(settings)
        else:
            logger.info("market_closed_skip_run", extra={"sleep_seconds": schedule.interval_seconds})
            run_maintenance_tasks(settings)
        if settings.run_once:
            return
        sleep_seconds = min(schedule.interval_seconds, settings.max_scheduler_sleep_seconds)
        time.sleep(max(sleep_seconds, 1))


if __name__ == "__main__":
    main()
