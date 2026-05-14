from __future__ import annotations

import re
from pathlib import Path
from string import Template
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from gemini_stock.config import load_settings
from gemini_stock.rules.movement_alerts import DEFAULT_MOVEMENT_ALERT_THRESHOLD_PCTS
from gemini_stock.rules.trade_plan import has_actionable_trade_levels
from gemini_stock.storage.db import Database
from gemini_stock.web.repository import DashboardRepository, utc_now_iso

WEB_DIR = Path(__file__).resolve().parent
STATIC_DIR = WEB_DIR / "static"
TEMPLATE_DIR = WEB_DIR / "templates"
DASHBOARD_TEMPLATE_PATH = TEMPLATE_DIR / "dashboard.html"


def create_app(
    database_path: str | Path | None = None,
    chart_dir: str | Path | None = None,
    symbols: list[str] | None = None,
) -> FastAPI:
    settings = load_settings()
    db_path = Path(database_path or settings.database_path)
    charts = Path(chart_dir or settings.chart_dir)
    app = FastAPI(title="Gemini Stock Dashboard")
    charts.mkdir(parents=True, exist_ok=True)
    app.mount("/charts", StaticFiles(directory=charts), name="charts")
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    repo = DashboardRepository(db_path, charts)
    db = Database(db_path)
    db.initialize()

    def selected_symbols() -> list[str]:
        if symbols is not None:
            return symbols
        if db is not None:
            dynamic = db.list_watch_symbols("primary")
            if dynamic:
                return dynamic
        return settings.symbols

    def watchlist_payload() -> dict[str, list[str]]:
        return {"primary": selected_symbols()}

    def movement_alert_settings_payload() -> dict:
        thresholds = db.get_movement_alert_threshold_settings()
        return {
            "threshold_pcts": thresholds["fast_drop"],
            "fast_drop": thresholds["fast_drop"],
            "fast_rise": thresholds["fast_rise"],
            "configurable": True,
        }

    def build_dashboard_payload() -> dict[str, Any]:
        symbols_now = selected_symbols()
        status = repo.get_status()
        metrics = repo.get_today_metrics(symbols_now)
        symbol_states = repo.get_symbol_states(symbols_now)
        llm_outputs = repo.get_recent_llm_outputs(symbols_now)
        alerts = repo.get_recent_alerts(symbols_now)
        errors = repo.get_recent_errors(symbols_now)
        watchlist = watchlist_payload()
        movement_alert_settings = movement_alert_settings_payload()
        priority_views = repo.build_priority_views(symbols_now)
        return {
            "generated_at_utc": utc_now_iso(),
            "status": status,
            "today_metrics": metrics,
            "symbols": symbol_states,
            "recent_llm_outputs": llm_outputs,
            "recent_alerts": alerts,
            "recent_errors": errors,
            "watchlist": watchlist,
            "movement_alert_settings": movement_alert_settings,
            "priority_views": priority_views,
        }

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        payload = build_dashboard_payload()
        return render_dashboard(
            status=payload["status"],
            metrics=payload["today_metrics"],
            symbols=payload["symbols"],
            llm_outputs=payload["recent_llm_outputs"],
            alerts=payload["recent_alerts"],
            errors=payload["recent_errors"],
            watchlist=payload["watchlist"],
            movement_alert_settings=payload["movement_alert_settings"],
            priority_views=payload["priority_views"],
        )

    @app.get("/api/status")
    def api_status() -> dict:
        return build_dashboard_payload()

    @app.get("/api/health")
    def api_health() -> dict:
        status = repo.get_status()
        return {
            "ok": not status["worker_health"]["is_stale"],
            "worker_health": status["worker_health"],
            "market_session": status["market_session"],
            "latest_llm_at": status["latest_llm_at"],
            "latest_alert_at": status.get("latest_alert_at"),
            "latest_worker_activity_at": status.get("latest_worker_activity_at"),
        }

    @app.get("/api/watchlist")
    def api_watchlist() -> dict:
        return {
            "watchlist": watchlist_payload(),
            "configurable": db is not None,
        }

    @app.get("/api/movement-alert-settings")
    def api_movement_alert_settings() -> dict:
        return movement_alert_settings_payload()

    @app.post("/api/movement-alert-settings")
    def api_update_movement_alert_settings(payload: dict) -> dict:
        raw_thresholds = (
            {"fast_drop": payload.get("fast_drop"), "fast_rise": payload.get("fast_rise")}
            if "fast_drop" in payload or "fast_rise" in payload
            else payload.get("threshold_pcts")
        )
        if not isinstance(raw_thresholds, (list, dict)):
            raise HTTPException(status_code=422, detail="movement thresholds must be a list or split threshold object")
        try:
            thresholds = db.save_movement_alert_thresholds(raw_thresholds)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        settings = db.get_movement_alert_threshold_settings()
        return {
            "ok": True,
            "threshold_pcts": settings["fast_drop"],
            "fast_drop": settings["fast_drop"],
            "fast_rise": settings["fast_rise"],
            "configurable": True,
        }

    @app.post("/api/watchlist")
    def api_add_watch_symbol(payload: dict) -> dict:
        symbol = str(payload.get("symbol") or "").upper().strip()
        if not re.fullmatch(r"[A-Z0-9._-]{1,10}", symbol):
            raise HTTPException(status_code=422, detail="symbol must be 1-10 chars and only contain A-Z, 0-9, dot, underscore, or hyphen")
        if not db.list_watch_symbols("primary"):
            for default_symbol in settings.symbols:
                db.add_watch_symbol(default_symbol, profile="primary")
        added_symbol = db.add_watch_symbol(symbol, profile="primary")
        return {
            "ok": True,
            "symbol": added_symbol,
            "watchlist": watchlist_payload(),
        }

    @app.delete("/api/watchlist/{symbol}")
    def api_remove_watch_symbol(symbol: str) -> dict:
        normalized_symbol = symbol.upper().strip()
        if not re.fullmatch(r"[A-Z0-9._-]{1,10}", normalized_symbol):
            raise HTTPException(status_code=422, detail="symbol must be 1-10 chars and only contain A-Z, 0-9, dot, underscore, or hyphen")
        removed = db.remove_watch_symbol(normalized_symbol, profile="primary")
        if not removed:
            raise HTTPException(status_code=404, detail="symbol is not in primary watchlist")
        return {
            "ok": True,
            "symbol": normalized_symbol,
            "watchlist": watchlist_payload(),
        }

    return app


def _fmt(value) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def _label(value: str | None) -> str:
    labels = {
        "overnight": "夜盘",
        "premarket": "盘前",
        "regular": "盘中",
        "afterhours": "盘后",
        "closed": "休市",
        "json_only": "JSON 扫描",
        "multimodal_review": "图像复核",
        "market_data": "行情检查",
        "bullish": "看多",
        "bearish": "看空",
        "neutral": "中性",
        "bullish_reversal": "看多反转",
        "bearish_breakdown": "看空破位",
        "breakout": "突破",
        "breakdown": "跌破",
        "no_trade": "观望",
        "movement_alert": "价格异动",
        "fast_drop": "快跌异动",
        "fast_rise": "快涨异动",
        "confirmed": "确认",
        "rejected": "拒绝",
        "not_applicable": "不适用",
        "waiting": "等待",
        "ok": "正常",
        "stale": "过期",
        "no_data": "无数据",
    }
    return labels.get(value or "", value or "-")


def _fmt_range(values) -> str:
    if not values or len(values) < 2:
        return "-"
    return f"{_fmt(values[0])} - {_fmt(values[1])}"


def _fmt_price_band(values, bias: str | None, descending_for_bearish: bool = False) -> str:
    if not values or len(values) < 2:
        return "-"
    first, second = values[0], values[1]
    if bias == "bearish":
        ordered = sorted([first, second], reverse=descending_for_bearish)
        return f"{_fmt(ordered[0])} - {_fmt(ordered[1])}"
    return f"{_fmt(first)} - {_fmt(second)}"


def _trade_labels(bias: str | None) -> tuple[str, str, str]:
    if bias == "bearish":
        return ("卖出参考", "风险位", "回补买入")
    return ("买入参考", "止损位", "卖出参考")


def _action_hint(bias: str | None) -> str:
    if bias == "bearish":
        return "持有偏谨慎，优先卖出或减仓"
    if bias == "bullish":
        return "可考虑分批买入，按计划止盈"
    return "先观望，等待更清晰信号"


def _action_label(bias: str | None) -> str:
    if bias == "bearish":
        return "减仓卖出"
    if bias == "bullish":
        return "买入关注"
    return "观望等待"


def _symbol_sort_key(item: dict) -> tuple[int, str]:
    action = _symbol_action_state(
        item,
        has_actionable_trade_levels(
            setup_type=item.get("setup_type"),
            entry_zone=item.get("entry_zone"),
            stop_loss=item.get("stop_loss"),
            take_profit=item.get("take_profit"),
            risk_reward_ratio=item.get("risk_reward_ratio"),
        ),
    )
    score = int(action["rank"])
    sentiment = item.get("sentiment_score")
    if isinstance(sentiment, (int, float)):
        score += min(int(abs(sentiment) * 2), 20)
    return (-score, str(item.get("symbol") or ""))


def _symbol_action_state(item: dict, actionable_plan: bool) -> dict:
    freshness = (item.get("data_freshness") or {}).get("state")
    bias = item.get("bias") or "neutral"
    if item.get("should_alert"):
        return {"label": "优先处理", "message": "AI 已触发提醒，先核对失效位和仓位", "tone": "urgent", "bias": bias, "rank": 100}
    if freshness in {"stale", "no_data"}:
        return {"label": "暂停判断", "message": "数据过期，暂停交易判断", "tone": "watch", "bias": "neutral", "rank": 10}
    if item.get("last_error"):
        return {"label": "检查错误", "message": "最近分析有异常，先看错误记录", "tone": "watch", "bias": "neutral", "rank": 15}

    price = item.get("latest_1m_price") or item.get("regular_market_price") or item.get("last_price")
    if actionable_plan and _price_in_zone(price, item.get("entry_zone")):
        if bias == "bearish":
            return {"label": "减风险", "message": "价格进入卖出参考区，先减风险", "tone": "urgent", "bias": bias, "rank": 92}
        return {"label": "到买入区", "message": "价格进入买入参考区，分批试探", "tone": "ready", "bias": bias, "rank": 92}
    if actionable_plan and _price_in_zone(price, item.get("take_profit")):
        if bias == "bearish":
            return {"label": "回补区", "message": "价格进入回补区，避免继续追空", "tone": "ready", "bias": bias, "rank": 88}
        return {"label": "止盈区", "message": "价格进入止盈参考区，按计划分批处理", "tone": "ready", "bias": bias, "rank": 88}
    if actionable_plan:
        return {"label": _action_label(bias), "message": _action_hint(bias), "tone": "watch", "bias": bias, "rank": 60}
    return {"label": "观察", "message": "当前不可执行，仅观察", "tone": "watch", "bias": "neutral", "rank": 30}


def _price_in_zone(price, zone) -> bool:
    if price is None or not zone or len(zone) < 2:
        return False
    low, high = sorted(float(value) for value in zone[:2])
    return low <= float(price) <= high


def _build_dashboard_template_values(
    status: dict,
    metrics: dict,
    symbols: list[dict],
    llm_outputs: list[dict],
    alerts: list[dict],
    errors: list[dict],
    watchlist: dict[str, list[str]],
    movement_alert_settings: dict,
    priority_views: dict[str, list[dict]] | None = None,
) -> dict[str, str]:
    ordered_symbols = sorted(symbols, key=_symbol_sort_key)
    symbol_cards = "\n".join(_render_symbol_card(item) for item in ordered_symbols)
    llm_mobile_cards = "\n".join(_render_llm_mobile_card(row) for row in llm_outputs[:8]) or "<div class='feed-card muted-row'>暂无 AI 分析记录</div>"
    alert_mobile_cards = "\n".join(_render_alert_mobile_card(row) for row in alerts[:8]) or "<div class='feed-card muted-row'>暂无报警记录</div>"
    metric_cards = "\n".join(
        f"<div class='stat'><span>{label}</span><b>{_fmt(value)}</b></div>"
        for label, value in [
            ("扫描轮次", metrics["estimated_run_cycles_today"]),
            ("标的扫描", metrics["symbol_scans_today"]),
            ("JSON 调用", metrics["json_only_calls_today"]),
            ("图像复核", metrics["multimodal_calls_today"]),
            ("传图次数", metrics["image_calls_today"]),
            ("报警次数", metrics["alerts_today"]),
            ("AI 错误", metrics["llm_errors_today"]),
            ("平均分", metrics["avg_sentiment_today"]),
        ]
    )
    warning_text = "、".join(status.get("config_warnings") or [])
    worker_state = _label((status.get("worker_health") or {}).get("state"))
    json_count = metrics["json_only_calls_today"]
    image_count = metrics["image_calls_today"]
    total_ai = max(json_count + image_count, 1)
    json_pct = max(int(json_count / total_ai * 100), 1)
    image_pct = max(int(image_count / total_ai * 100), 1)
    llm_rows = "\n".join(
        f"""
        <tr>
          <td>{row['created_at_utc']}</td><td>{row['symbol']}</td><td>{_label(row['analysis_level'])}</td>
          <td>{'是' if row['has_image'] else '否'}</td><td>{_label(row['bias'])}</td>
          <td>{_fmt(row['sentiment_score'])}</td><td>{_fmt(row['confidence'])}</td>
          <td>{_label(row['visual_confirmation'])}</td><td>{_fmt(row['error'])}</td>
        </tr>
        """
        for row in llm_outputs
    ) or "<tr><td colspan='9'>暂无 AI 分析记录</td></tr>"
    alert_rows = "\n".join(
        f"""
        <tr>
          <td>{row['created_at_utc']}</td><td><span class=\"feed-type\">{row.get('category', '系统记录')}</span></td><td>{row['symbol']}</td><td>{row['channel']}</td>
          <td>{_label(row['bias'])}</td><td>{_fmt(row['sentiment_score'])}</td><td>{_fmt(row['confidence'])}</td>
        </tr>
        """
        for row in alerts
    ) or "<tr><td colspan='7'>暂无报警记录</td></tr>"
    error_items = "\n".join(
        f"<li><b>{row['symbol']}</b><span>{row['created_at_utc']} · {_label(row['analysis_level'])}</span><p>{row['error']}</p></li>"
        for row in errors
    ) or "<li class='muted-row'>暂无近期错误</li>"
    priority_views = priority_views or {}
    priority_sections = [
        ("最紧急", priority_views.get("most_urgent", [])),
        ("最异常", priority_views.get("most_abnormal", [])),
        ("最可执行", priority_views.get("most_actionable", [])),
    ]
    priority_cards = "".join(
        f"""
        <div class="priority-group">
          <h3>{title}</h3>
          <div class="priority-list">
            {''.join(_render_priority_item(item) for item in items) or "<div class='priority-item muted-row'>暂无数据</div>"}
          </div>
        </div>
        """
        for title, items in priority_sections
    )
    primary_watch_items = "".join(
        f"""
        <div class="watch-chip">
          <span class="watch-badge">{symbol}</span>
          <button class="watch-remove" type="button" data-remove-symbol="{symbol}" aria-label="移除 {symbol}">移除</button>
        </div>
        """
        for symbol in watchlist.get("primary", [])
    ) or "<span class='watch-badge'>暂无</span>"
    drop_thresholds = movement_alert_settings.get("fast_drop") or movement_alert_settings.get("threshold_pcts") or DEFAULT_MOVEMENT_ALERT_THRESHOLD_PCTS
    rise_thresholds = movement_alert_settings.get("fast_rise") or movement_alert_settings.get("threshold_pcts") or DEFAULT_MOVEMENT_ALERT_THRESHOLD_PCTS
    return {
        "TOP_STATUS": (
            f'<div class="metric primary"><span>距离下次执行</span><b id="next-run-countdown" data-seconds="{int(status.get("countdown_seconds") or 0)}">{status["countdown_label"]}</b></div>'
            f'<div class="metric"><span>市场阶段</span><b>{_label(status["market_session"])}</b></div>'
            f'<div class="metric"><span>今日报警 / 错误</span><b>{metrics["alerts_today"]} / {metrics["llm_errors_today"]}</b></div>'
            f'<div class="metric"><span>后台状态</span><b>{worker_state}</b></div>'
        ),
        "PRIORITY_CARDS": priority_cards,
        "CONFIG_WARNING": f"配置提醒：本地 .env 含敏感字段 {warning_text}，建议轮换并移入密钥管理。" if warning_text else "",
        "WATCH_PRIMARY": " · ".join(watchlist.get("primary") or []) or "-",
        "PRIMARY_WATCH_ITEMS": primary_watch_items,
        "DROP_TIER_1": str(drop_thresholds[0]),
        "DROP_TIER_2": str(drop_thresholds[1]),
        "DROP_TIER_3": str(drop_thresholds[2]),
        "RISE_TIER_1": str(rise_thresholds[0]),
        "RISE_TIER_2": str(rise_thresholds[1]),
        "RISE_TIER_3": str(rise_thresholds[2]),
        "SYMBOL_CARDS": symbol_cards,
        "METRIC_CARDS": metric_cards,
        "COST_PANEL": (
            '<h2>AI 调用成本代理</h2>'
            f'<div class="bar" aria-label="JSON 与图像调用比例" style="grid-template-columns: {json_pct}fr {image_pct}fr;"><i></i><i></i></div>'
            f'<div class="legend"><span>JSON-only {json_count} 次</span><span>传图 {image_count} 次</span></div>'
            "<p class=\"sub\">日常扫描优先 JSON，强候选才进入图像复核。</p>"
        ),
        "LLM_ROWS": llm_rows,
        "LLM_MOBILE_CARDS": llm_mobile_cards,
        "ERROR_ITEMS": error_items,
        "ALERT_ROWS": alert_rows,
        "ALERT_MOBILE_CARDS": alert_mobile_cards,
    }


def render_dashboard(
    status: dict,
    metrics: dict,
    symbols: list[dict],
    llm_outputs: list[dict],
    alerts: list[dict],
    errors: list[dict],
    watchlist: dict[str, list[str]],
    movement_alert_settings: dict,
    priority_views: dict[str, list[dict]] | None = None,
) -> str:
    values = _build_dashboard_template_values(
        status=status,
        metrics=metrics,
        symbols=symbols,
        llm_outputs=llm_outputs,
        alerts=alerts,
        errors=errors,
        watchlist=watchlist,
        movement_alert_settings=movement_alert_settings,
        priority_views=priority_views,
    )
    return _render_dashboard_template(values)


def _render_dashboard_template(values: dict[str, str]) -> str:
    template = Template(DASHBOARD_TEMPLATE_PATH.read_text(encoding="utf-8"))
    return template.safe_substitute(values)


def _render_symbol_card(item: dict) -> str:
    events = "".join(f"<span class='event'>{event}</span>" for event in item["technical_events"]) or "<span class='event'>暂无事件</span>"
    chart = f"<img class='chart' src='{item['chart_path']}' alt='{item['symbol']} chart'>" if item["chart_path"] else ""
    bias = item["bias"] or "neutral"
    score_value = item["sentiment_score"]
    score_class = "score negative" if isinstance(score_value, (int, float)) and score_value < 0 else "score"
    entry_label, stop_label, target_label = _trade_labels(item["bias"])
    actionable_plan = has_actionable_trade_levels(
        setup_type=item.get("setup_type"),
        entry_zone=item.get("entry_zone"),
        stop_loss=item.get("stop_loss"),
        take_profit=item.get("take_profit"),
        risk_reward_ratio=item.get("risk_reward_ratio"),
    )
    entry_value = _fmt_price_band(item["entry_zone"], item["bias"])
    stop_value = _fmt(item["stop_loss"])
    target_value = _fmt_price_band(item["take_profit"], item["bias"])
    trade_box_class = "trade-box" if actionable_plan else "trade-box muted"
    plan_note = "" if actionable_plan else "<div class='plan-note'>当前不可执行，仅观察</div>"
    action = _symbol_action_state(item, actionable_plan)
    return f"""
    <article class="card" data-symbol="{item['symbol']}">
      <div class="card-head">
        <div class="headline">
          <div class="symbol">{item['symbol']}</div>
          <div class="meta-row">
            <span class="pill bias-{bias}">{_label(item['bias'])}</span>
            <span class="pill">{_label(item['setup_type'])}</span>
            <span class="pill">{_label(item['analysis_level'])}</span>
            <span class="pill">{_action_label(item['bias'])}</span>
          </div>
        </div>
        <div class="score-wrap">
          <div class="{score_class}">{_fmt(score_value)}</div>
          <div class="confidence">置信度 {_fmt(item['confidence'])}</div>
        </div>
      </div>
      <div class="action-banner {action['tone']}">
        <div class="action-copy">
          <span>当前动作 / 建议动作</span>
          <b>{action['message']}</b>
        </div>
        <div class="action-tag {action['bias']}">{action['label']}</div>
      </div>
      <div class="trade-strip">
        <div class="{trade_box_class}"><span>{entry_label}</span><b>{entry_value}</b></div>
        <div class="{trade_box_class}"><span>{stop_label}</span><b>{stop_value}</b></div>
        <div class="{trade_box_class}"><span>{target_label}</span><b>{target_value}</b></div>
      </div>
      {plan_note}
      <div class="grid">
        <div class="cell"><span>主交易收盘</span>{_fmt(item['regular_market_price'])}</div>
        <div class="cell"><span>盘后价</span>{_fmt(item['post_market_price'])}</div>
        <div class="cell"><span>RSI</span>{_fmt(item['rsi_14'])}</div>
        <div class="cell"><span>EMA50</span>{_fmt(item['ema_50'])}</div>
        <div class="cell"><span>VWAP</span>{_fmt(item['vwap'])}</div>
        <div class="cell"><span>ATR</span>{_fmt(item['atr_14'])}</div>
        <div class="cell"><span>风险收益比</span>{_fmt(item['risk_reward_ratio'])}</div>
        <div class="cell"><span>级别</span>{_label(item['analysis_level'])}</div>
        <div class="cell"><span>传图</span>{'是' if item['has_image'] else '否'}</div>
        <div class="cell"><span>视觉复核</span>{_label(item['visual_confirmation'])}</div>
        <div class="cell"><span>收盘时间</span>{_fmt(item['regular_market_time'])}</div>
        <div class="cell"><span>盘后时间</span>{_fmt(item['post_market_time'])}</div>
        <div class="cell"><span>技术快照时间</span>{_fmt(item['feature_timestamp'])}</div>
        <div class="cell"><span>最新 1m K</span>{_fmt(item.get('latest_1m_price'))} · {_fmt(item.get('latest_1m_timestamp'))}</div>
        <div class="cell"><span>最新 15m K</span>{_fmt(item.get('latest_15m_price'))} · {_fmt(item.get('latest_15m_timestamp'))}</div>
        <div class="cell"><span>最近异常</span>{_fmt(item['last_error'])}</div>
      </div>
      <div class="events">{events}</div>
      {chart}
    </article>
    """


def _render_llm_mobile_card(row: dict) -> str:
    return f"""
    <div class="feed-card">
      <div class="feed-top">
        <div>
          <b>{row['symbol']}</b>
          <div class="feed-meta">{row['created_at_utc']}</div>
        </div>
        <span class="pill bias-{row['bias'] or 'neutral'}">{_label(row['bias'])}</span>
      </div>
      <div class="feed-meta">{_label(row['analysis_level'])} · 传图 {'是' if row['has_image'] else '否'} · 视觉复核 {_label(row['visual_confirmation'])}</div>
      <div>分数 {_fmt(row['sentiment_score'])} · 置信度 {_fmt(row['confidence'])}</div>
      <div class="feed-meta">{_fmt(row['error']) if row['error'] else '无异常'}</div>
    </div>
    """


def _render_priority_item(item: dict) -> str:
    return f"""
    <div class="priority-item">
      <b>{item.get('symbol', '-')}</b>
      <div class="priority-meta">
        <span>{_label(item.get('bias'))} · {_label(item.get('setup_type'))}</span>
        <span class="priority-score">{_fmt(item.get('score'))}</span>
      </div>
      <div class="priority-meta">
        <span>新鲜度 {_label(item.get('freshness'))}</span>
        <span>{item.get('distance_label') or '位置距离 -'}</span>
      </div>
      <div class="feed-meta">{item.get('explanation') or '当前关注度相对更高'}</div>
    </div>
    """


def _render_alert_mobile_card(row: dict) -> str:
    return f"""
    <div class="feed-card">
      <div class="feed-top">
        <div>
          <b>{row['symbol']}</b>
          <div class="feed-meta">{row['created_at_utc']}</div>
        </div>
        <span class="pill bias-{row['bias'] or 'neutral'}">{_label(row['bias'])}</span>
      </div>
      <div class="feed-meta"><span class="feed-type">{row.get('category', '系统记录')}</span> · {row['channel']} · {_label(row['setup_type'])}</div>
      <div>分数 {_fmt(row['sentiment_score'])} · 置信度 {_fmt(row['confidence'])}</div>
    </div>
    """


app = create_app()


def main() -> None:
    import uvicorn

    uvicorn.run("gemini_stock.web.app:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    main()
