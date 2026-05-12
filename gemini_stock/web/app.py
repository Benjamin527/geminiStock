from __future__ import annotations

import re
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from gemini_stock.config import load_settings
from gemini_stock.rules.movement_alerts import DEFAULT_MOVEMENT_ALERT_THRESHOLD_PCTS
from gemini_stock.rules.trade_plan import has_actionable_trade_levels
from gemini_stock.storage.db import Database
from gemini_stock.web.repository import DashboardRepository, utc_now_iso


def create_app(
    database_path: str | Path | None = None,
    chart_dir: str | Path | None = None,
    symbols: list[str] | None = None,
    benchmark_symbols: list[str] | None = None,
) -> FastAPI:
    settings = load_settings()
    db_path = Path(database_path or settings.database_path)
    charts = Path(chart_dir or settings.chart_dir)
    app = FastAPI(title="Gemini Stock Dashboard")
    charts.mkdir(parents=True, exist_ok=True)
    app.mount("/charts", StaticFiles(directory=charts), name="charts")
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

    def selected_benchmarks() -> list[str]:
        if benchmark_symbols is not None:
            return benchmark_symbols
        if db is not None:
            dynamic = db.list_watch_symbols("benchmark")
            if dynamic:
                return dynamic
        return settings.benchmark_symbols

    def watchlist_payload() -> dict[str, list[str]]:
        return {
            "primary": selected_symbols(),
            "benchmark": selected_benchmarks(),
        }

    def movement_alert_settings_payload() -> dict:
        thresholds = db.get_movement_alert_threshold_settings()
        return {
            "threshold_pcts": thresholds["fast_drop"],
            "fast_drop": thresholds["fast_drop"],
            "fast_rise": thresholds["fast_rise"],
            "configurable": True,
        }

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        symbols_now = selected_symbols()
        benchmarks_now = selected_benchmarks()
        return render_dashboard(
            status=repo.get_status(),
            metrics=repo.get_today_metrics(symbols_now),
            symbols=repo.get_symbol_states(symbols_now),
            benchmarks=repo.get_benchmark_states(benchmarks_now),
            llm_outputs=repo.get_recent_llm_outputs(symbols_now),
            alerts=repo.get_recent_alerts(symbols_now + benchmarks_now),
            errors=repo.get_recent_errors(symbols_now),
            watchlist=watchlist_payload(),
            movement_alert_settings=movement_alert_settings_payload(),
            priority_views=repo.build_priority_views(symbols_now, benchmarks_now),
        )

    @app.get("/api/status")
    def api_status() -> dict:
        symbols_now = selected_symbols()
        benchmarks_now = selected_benchmarks()
        return {
            "generated_at_utc": utc_now_iso(),
            "status": repo.get_status(),
            "today_metrics": repo.get_today_metrics(symbols_now),
            "symbols": repo.get_symbol_states(symbols_now),
            "benchmarks": repo.get_benchmark_states(benchmarks_now),
            "recent_llm_outputs": repo.get_recent_llm_outputs(symbols_now),
            "recent_alerts": repo.get_recent_alerts(symbols_now + benchmarks_now),
            "recent_errors": repo.get_recent_errors(symbols_now),
            "watchlist": watchlist_payload(),
            "movement_alert_settings": movement_alert_settings_payload(),
            "priority_views": repo.build_priority_views(symbols_now, benchmarks_now),
        }

    @app.get("/api/health")
    def api_health() -> dict:
        status = repo.get_status()
        return {
            "ok": not status["worker_health"]["is_stale"],
            "worker_health": status["worker_health"],
            "market_session": status["market_session"],
            "latest_llm_at": status["latest_llm_at"],
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


def render_dashboard(
    status: dict,
    metrics: dict,
    symbols: list[dict],
    benchmarks: list[dict],
    llm_outputs: list[dict],
    alerts: list[dict],
    errors: list[dict],
    watchlist: dict[str, list[str]],
    movement_alert_settings: dict,
    priority_views: dict[str, list[dict]] | None = None,
) -> str:
    ordered_symbols = sorted(symbols, key=_symbol_sort_key)
    symbol_cards = "\n".join(_render_symbol_card(item) for item in ordered_symbols)
    benchmark_cards = "\n".join(_render_benchmark_card(item) for item in benchmarks)
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
          <td>{row['created_at_utc']}</td><td><span class="feed-type">{row.get('category', '系统记录')}</span></td><td>{row['symbol']}</td><td>{row['channel']}</td>
          <td>{_label(row['bias'])}</td><td>{_fmt(row['sentiment_score'])}</td><td>{_fmt(row['confidence'])}</td>
        </tr>
        """
        for row in alerts
    ) or "<tr><td colspan='7'>暂无报警记录</td></tr>"
    error_items = "\n".join(
        f"<li><b>{row['symbol']}</b><span>{row['created_at_utc']} · {_label(row['analysis_level'])}</span><p>{row['error']}</p></li>"
        for row in errors
    ) or "<li class='muted-row'>暂无近期错误</li>"
    monitored_primary = " · ".join(watchlist.get("primary") or [])
    monitored_benchmark = " · ".join(watchlist.get("benchmark") or [])
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
    countdown_seconds = int(status.get("countdown_seconds") or 0)
    return f"""
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="30">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=Sora:wght@600;700&display=swap" rel="stylesheet">
  <title>美股 AI 盯盘控制台</title>
  <style>
    :root {{
      --bg: #0f141a; --panel: #17202a; --ink: #e8edf4; --muted: #9fb0c4;
      --line: #273647; --green: #1bc18f; --red: #ef6f7a; --amber: #f5b94c; --blue: #66b7ff;
      --soft: #1e2b39; --dark: #0a1016;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: radial-gradient(circle at 8% 0%, #1a2633 0, #0f141a 38%), var(--bg); color: var(--ink); font: 14px/1.5 "IBM Plex Sans", "PingFang SC", "Microsoft YaHei", sans-serif; }}
    header {{ padding: 22px 32px 14px; border-bottom: 1px solid var(--line); background: linear-gradient(180deg, #121a24, #0e151d); }}
    h1 {{ margin: 0; font-size: clamp(22px, 4vw, 34px); letter-spacing: 0; font-family: "Sora", "IBM Plex Sans", sans-serif; }}
    h2 {{ margin: 0 0 12px; font-size: 16px; }}
    .sub {{ color: var(--muted); margin-top: 4px; }}
    .watch-panel {{ display: grid; gap: 10px; grid-template-columns: minmax(0, 1fr) auto; align-items: end; }}
    .watch-meta {{ display: grid; gap: 8px; }}
    .watch-badges {{ display: flex; gap: 8px; flex-wrap: wrap; }}
    .watch-chip {{ display: inline-flex; align-items: center; gap: 6px; }}
    .watch-badge {{ border: 1px solid var(--line); border-radius: 999px; padding: 4px 10px; font-size: 12px; color: var(--ink); background: rgba(102,183,255,.08); }}
    .watch-remove {{
      border: 1px solid rgba(255,255,255,.14);
      border-radius: 999px;
      background: rgba(255,255,255,.04);
      color: var(--muted);
      font-size: 12px;
      padding: 4px 8px;
      cursor: pointer;
    }}
    .watch-remove:hover {{ color: var(--ink); border-color: rgba(255,255,255,.24); }}
    .watch-form {{ display: flex; gap: 8px; flex-wrap: wrap; justify-content: flex-end; }}
    .watch-input {{
      width: 180px;
      height: 36px;
      border-radius: 8px;
      border: 1px solid var(--line);
      background: #0d1722;
      color: var(--ink);
      padding: 0 10px;
      text-transform: uppercase;
      letter-spacing: 0;
    }}
    .watch-btn {{
      height: 36px;
      border: 1px solid rgba(27,193,143,.45);
      border-radius: 8px;
      background: linear-gradient(180deg, rgba(27,193,143,.28), rgba(27,193,143,.15));
      color: #d8ffef;
      font-weight: 600;
      padding: 0 14px;
      cursor: pointer;
    }}
    .watch-btn:hover {{ filter: brightness(1.08); }}
    details.settings-drawer {{ padding: 0; overflow: hidden; }}
    details.settings-drawer > summary {{
      list-style: none; cursor: pointer; padding: 15px 16px; display: flex; align-items: center; justify-content: space-between; gap: 12px;
      color: var(--ink); font-weight: 700;
    }}
    details.settings-drawer > summary::-webkit-details-marker {{ display: none; }}
    details.settings-drawer > summary::after {{ content: "展开"; color: var(--muted); font-size: 12px; font-weight: 600; }}
    details.settings-drawer[open] > summary::after {{ content: "收起"; }}
    .settings-body {{ display: grid; gap: 14px; padding: 0 14px 14px; }}
    .movement-settings {{ display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 12px; align-items: end; }}
    .threshold-matrix {{ display: grid; grid-template-columns: 90px repeat(3, minmax(110px, 1fr)); gap: 10px; align-items: end; }}
    .threshold-label {{ color: var(--muted); font-weight: 700; padding-bottom: 10px; }}
    .tier-grid {{ display: grid; grid-template-columns: repeat(3, minmax(110px, 1fr)); gap: 10px; }}
    .tier-field {{ display: grid; gap: 5px; }}
    .tier-field label {{ color: var(--muted); font-size: 12px; }}
    .tier-input {{
      height: 36px;
      border-radius: 8px;
      border: 1px solid var(--line);
      background: #0d1722;
      color: var(--ink);
      padding: 0 10px;
    }}
    main {{ padding: 24px 32px 40px; display: grid; gap: 18px; }}
    .top-deck {{
      position: sticky;
      top: 0;
      z-index: 20;
      background: rgba(15,20,26,.94);
      backdrop-filter: blur(16px);
      padding: 6px 0 2px;
      border-bottom: 1px solid rgba(255,255,255,.05);
    }}
    .status {{ display: grid; grid-template-columns: 1.1fr .95fr .95fr 1.2fr; gap: 10px; }}
    .metric, section, .card, .stat, .cost-panel, .errors {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 14px; box-shadow: 0 10px 30px rgba(4,9,14,.2); }}
    .metric.primary {{ background: var(--dark); color: #f6f8f4; border-color: var(--line); }}
    .top-deck .metric {{
      padding: 10px 12px;
      border-radius: 10px;
      box-shadow: 0 6px 18px rgba(4,9,14,.16);
    }}
    .top-deck .metric b {{ display: block; font-size: clamp(16px, 2.4vw, 22px); margin-top: 2px; line-height: 1.1; }}
    .top-deck .metric span {{ color: var(--muted); display: block; font-size: 11px; letter-spacing: .01em; }}
    .metric b {{ display: block; font-size: clamp(18px, 3vw, 26px); margin-top: 3px; line-height: 1.15; }}
    .metric span, .stat span {{ color: var(--muted); display: block; font-size: 12px; }}
    .metric.primary span {{ color: #b9c4bd; }}
    .overview {{ display: grid; grid-template-columns: minmax(0, 2fr) minmax(280px, 1fr); gap: 12px; }}
    .priority-board {{ margin-top: 12px; display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }}
    .priority-group {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 12px; }}
    .priority-group h3 {{ margin: 0 0 10px; font-size: 14px; color: var(--muted); }}
    .priority-list {{ display: grid; gap: 8px; }}
    .priority-item {{ border: 1px solid var(--line); border-radius: 8px; padding: 10px; background: #121b27; }}
    .priority-item b {{ display: block; font-size: 15px; }}
    .priority-meta {{ display: flex; justify-content: space-between; gap: 8px; color: var(--muted); font-size: 12px; margin-top: 4px; }}
    .priority-score {{ color: var(--ink); font-weight: 700; }}
    .stats {{ display: grid; grid-template-columns: repeat(4, minmax(110px, 1fr)); gap: 10px; }}
    .stat b {{ display: block; margin-top: 2px; font-size: 24px; }}
    .cost-panel h2 {{ margin-bottom: 10px; }}
    .bar {{ height: 14px; border: 1px solid var(--line); background: #101923; display: grid; grid-template-columns: {json_pct}fr {image_pct}fr; overflow: hidden; border-radius: 999px; }}
    .bar i:first-child {{ background: var(--green); }}
    .bar i:last-child {{ background: var(--amber); }}
    .legend {{ display: flex; justify-content: space-between; gap: 10px; color: var(--muted); font-size: 12px; margin-top: 8px; }}
    .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(340px, 1fr)); gap: 16px; }}
    .benchmark-cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 12px; }}
    .mobile-feed {{ display: none; gap: 10px; }}
    .mobile-tabs {{ display: none; position: sticky; top: 0; z-index: 30; padding: 8px; border: 1px solid var(--line); border-radius: 8px; background: rgba(10,16,22,.94); backdrop-filter: blur(14px); grid-template-columns: repeat(4, 1fr); gap: 6px; }}
    .mobile-tab {{ min-height: 36px; border: 1px solid var(--line); border-radius: 7px; background: #111b26; color: var(--muted); font-weight: 700; }}
    .mobile-tab.active {{ color: var(--ink); border-color: rgba(27,193,143,.45); background: rgba(27,193,143,.12); }}
    .card {{ background:
        radial-gradient(circle at top right, rgba(102,183,255,.12), transparent 28%),
        linear-gradient(180deg, #18222e, #141d28); }}
    .benchmark-card {{
      background: linear-gradient(180deg, #1a2431, #141d28);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
      box-shadow: 0 1px 0 rgba(20,30,25,.03);
    }}
    .benchmark-head {{ display: flex; justify-content: space-between; align-items: flex-start; gap: 10px; margin-bottom: 10px; }}
    .benchmark-head b {{ font-size: 22px; }}
    .benchmark-note {{ color: var(--ink); margin: 10px 0 12px; font-size: 13px; }}
    .benchmark-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }}
    .scenario-group {{ margin-top: 10px; border-top: 1px solid var(--line); padding-top: 10px; }}
    .scenario-group summary {{ cursor: pointer; color: var(--muted); font-size: 12px; }}
    .scenario-list {{ margin-top: 8px; display: grid; gap: 8px; }}
    .card-head {{ display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; margin-bottom: 12px; }}
    .symbol {{ font-size: 24px; font-weight: 700; }}
    .headline {{ display: flex; flex-direction: column; gap: 8px; }}
    .meta-row {{ display: flex; gap: 8px; flex-wrap: wrap; }}
    .pill {{ display: inline-flex; align-items: center; padding: 4px 10px; border-radius: 999px; border: 1px solid var(--line); background: rgba(255,255,255,.03); font-size: 12px; color: var(--muted); }}
    .pill.bias-bullish {{ background: rgba(13,119,86,.12); color: var(--green); border-color: rgba(13,119,86,.22); }}
    .pill.bias-bearish {{ background: rgba(182,61,50,.10); color: var(--red); border-color: rgba(182,61,50,.22); }}
    .pill.bias-neutral {{ background: rgba(36,92,131,.08); color: var(--blue); border-color: rgba(36,92,131,.2); }}
    .score-wrap {{ text-align: right; min-width: 88px; }}
    .score {{ font-size: 30px; font-weight: 700; line-height: 1; color: var(--green); }}
    .score.negative {{ color: var(--red); }}
    .confidence {{ color: var(--muted); font-size: 12px; margin-top: 4px; }}
    .action-banner {{
      display: flex; align-items: center; justify-content: space-between; gap: 12px;
      padding: 10px 12px; margin: 0 0 12px; border: 1px solid var(--line); border-radius: 8px;
      background: linear-gradient(180deg, rgba(255,255,255,.05), rgba(255,255,255,.01));
    }}
    .action-copy {{ min-width: 0; }}
    .action-copy span {{ display: block; color: var(--muted); font-size: 12px; }}
    .action-copy b {{ display: block; font-size: 17px; line-height: 1.25; margin-top: 2px; }}
    .action-tag {{
      flex: 0 0 auto; display: inline-flex; align-items: center; justify-content: center;
      min-width: 88px; padding: 8px 12px; border-radius: 999px; border: 1px solid var(--line);
      background: rgba(255,255,255,.03); font-size: 13px; font-weight: 600;
    }}
    .action-tag.bullish {{ color: var(--green); border-color: rgba(13,119,86,.25); background: rgba(13,119,86,.08); }}
    .action-tag.bearish {{ color: var(--red); border-color: rgba(182,61,50,.25); background: rgba(182,61,50,.08); }}
    .action-tag.neutral {{ color: var(--blue); border-color: rgba(36,92,131,.25); background: rgba(36,92,131,.08); }}
    .action-banner.urgent {{ border-color: rgba(239,111,122,.45); background: rgba(239,111,122,.10); }}
    .action-banner.ready {{ border-color: rgba(27,193,143,.45); background: rgba(27,193,143,.10); }}
    .action-banner.watch {{ border-color: rgba(245,185,76,.42); background: rgba(245,185,76,.08); }}
    .trade-strip {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; margin: 0 0 12px; }}
    .trade-box {{ padding: 10px 12px; border: 1px solid var(--line); border-radius: 8px; background: #12202e; min-width: 0; }}
    .trade-box span {{ display: block; color: var(--muted); font-size: 12px; }}
    .trade-box b {{ display: block; margin-top: 3px; font-size: 15px; line-height: 1.3; overflow-wrap: anywhere; }}
    .trade-box.muted {{ background: #111923; border-color: rgba(159,176,196,.25); }}
    .trade-box.muted b {{ color: var(--muted); }}
    .plan-note {{
      margin: -4px 0 12px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.4;
    }}
    .grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; }}
    .cell {{ border-top: 1px solid var(--line); padding-top: 8px; min-width: 0; }}
    .cell span {{ color: var(--muted); display: block; font-size: 12px; }}
    .events {{ margin-top: 12px; display: flex; gap: 6px; flex-wrap: wrap; }}
    .event {{ padding: 3px 7px; border: 1px solid var(--line); border-radius: 999px; color: var(--muted); background: rgba(255,255,255,.03); font-size: 12px; }}
    .chart {{ margin-top: 12px; max-width: 100%; border: 1px solid var(--line); border-radius: 6px; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ text-align: left; padding: 9px 8px; border-top: 1px solid var(--line); vertical-align: top; }}
    th {{ color: var(--muted); font-weight: 600; }}
    .split {{ display: grid; grid-template-columns: minmax(0, 1.4fr) minmax(280px, .8fr); gap: 12px; align-items: start; }}
    .feed-card {{
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #121b27;
      padding: 12px;
      display: grid;
      gap: 6px;
    }}
    .feed-top {{ display: flex; justify-content: space-between; gap: 10px; align-items: flex-start; }}
    .feed-top b {{ font-size: 15px; }}
    .feed-meta {{ color: var(--muted); font-size: 12px; }}
    .feed-type {{ display: inline-flex; width: fit-content; padding: 3px 8px; border-radius: 999px; border: 1px solid var(--line); color: var(--muted); font-size: 12px; }}
    .errors ul {{ list-style: none; padding: 0; margin: 0; display: grid; gap: 10px; }}
    .errors li {{ border-top: 1px solid var(--line); padding-top: 10px; }}
    .errors li:first-child {{ border-top: 0; padding-top: 0; }}
    .errors span {{ display: block; color: var(--muted); font-size: 12px; }}
    .errors p {{ margin: 4px 0 0; color: var(--red); overflow-wrap: anywhere; }}
    .muted-row {{ color: var(--muted); }}
    @media (max-width: 980px) {{
      .status, .overview, .split {{ grid-template-columns: 1fr; }}
      .priority-board {{ grid-template-columns: 1fr; }}
      .stats {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .watch-panel {{ grid-template-columns: 1fr; }}
      .watch-form {{ justify-content: flex-start; }}
      .movement-settings {{ grid-template-columns: 1fr; }}
      header, main {{ padding-left: 16px; padding-right: 16px; }}
      table {{ display: block; overflow-x: auto; white-space: nowrap; }}
    }}
    @media (max-width: 680px) {{
      .mobile-tabs {{ display: grid; }}
      [data-panel] {{ display: none; }}
      [data-panel].active-panel {{ display: block; }}
      .top-deck.active-panel {{ display: block; }}
      .overview.active-panel, .split.active-panel {{ display: grid; }}
      .top-deck {{ padding: 4px 0 0; }}
      .status {{ grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }}
      .top-deck .metric {{ padding: 9px 10px; }}
      .top-deck .metric b {{ font-size: 16px; }}
      .top-deck .metric span {{ font-size: 10px; }}
      .desktop-table {{ display: none; }}
      .mobile-feed {{ display: grid; }}
      .benchmark-cards {{ grid-template-columns: 1fr; }}
      .benchmark-grid {{ grid-template-columns: 1fr 1fr; }}
      .action-banner {{ align-items: flex-start; flex-direction: column; }}
      .action-tag {{ min-width: 0; }}
    }}
    @media (max-width: 520px) {{
      .cards {{ grid-template-columns: 1fr; }}
      .trade-strip {{ grid-template-columns: 1fr; }}
      .grid {{ grid-template-columns: 1fr 1fr; }}
      .stats {{ grid-template-columns: 1fr 1fr; }}
      .status {{ grid-template-columns: 1fr 1fr; }}
      .tier-grid {{ grid-template-columns: 1fr; }}
      .threshold-matrix {{ grid-template-columns: 1fr; }}
      .threshold-label {{ padding-bottom: 0; }}
      .metric, section, .card, .stat, .cost-panel, .errors {{ padding: 12px; }}
      .benchmark-grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>美股 AI 盯盘控制台</h1>
    <div class="sub">只读监控页面，不提供交易和下单操作。页面每 30 秒自动刷新。</div>
  </header>
  <main>
    <nav class="mobile-tabs" aria-label="Dashboard sections">
      <button class="mobile-tab active" type="button" data-tab-target="main">主观察</button>
      <button class="mobile-tab" type="button" data-tab-target="market">大盘</button>
      <button class="mobile-tab" type="button" data-tab-target="activity">告警</button>
      <button class="mobile-tab" type="button" data-tab-target="settings">设置</button>
    </nav>
    <section class="top-deck active-panel" data-panel="main">
      <div class="status">
        <div class="metric primary"><span>距离下次执行</span><b id="next-run-countdown" data-seconds="{countdown_seconds}">{status['countdown_label']}</b></div>
        <div class="metric"><span>市场阶段</span><b>{_label(status['market_session'])}</b></div>
        <div class="metric"><span>今日报警 / 错误</span><b>{metrics['alerts_today']} / {metrics['llm_errors_today']}</b></div>
        <div class="metric"><span>后台状态</span><b>{worker_state}</b></div>
      </div>
    </section>
    <section class="active-panel" data-panel="main">
      <h2>优先关注</h2>
      <div class="priority-board">
        {priority_cards}
      </div>
    </section>
    <div class="sub">{'配置提醒：本地 .env 含敏感字段 ' + warning_text + '，建议轮换并移入密钥管理。' if warning_text else ''}</div>
    <details class="settings-drawer" data-panel="settings">
      <summary>监控与阈值设置</summary>
      <div class="settings-body">
        <section>
          <h2>监控列表</h2>
          <div class="watch-panel">
            <div class="watch-meta">
              <div class="sub">主监控：{monitored_primary or '-'}</div>
              <div class="sub">参考监控：{monitored_benchmark or '-'}</div>
              <div class="watch-badges">{primary_watch_items}</div>
            </div>
            <form class="watch-form" id="watch-form">
              <input class="watch-input" id="watch-symbol" name="symbol" placeholder="输入代码，如 NVDA" maxlength="10" required>
              <button class="watch-btn" type="submit">加入监控</button>
            </form>
          </div>
        </section>
        <section>
          <h2>价格异动阈值</h2>
          <div class="movement-settings">
            <div>
              <div class="sub">快跌和快涨分别设置三档；1档推送 1 次，2档推送 2 次，3档推送 3 次。</div>
              <form class="threshold-matrix" id="movement-settings-form">
                <div class="threshold-label">下跌</div>
                <div class="tier-field">
                  <label for="drop-tier1-pct">下跌 1档 %</label>
                  <input class="tier-input" id="drop-tier1-pct" type="number" min="0.1" max="99" step="0.1" value="{drop_thresholds[0]}" required>
                </div>
                <div class="tier-field">
                  <label for="drop-tier2-pct">下跌 2档 %</label>
                  <input class="tier-input" id="drop-tier2-pct" type="number" min="0.1" max="99" step="0.1" value="{drop_thresholds[1]}" required>
                </div>
                <div class="tier-field">
                  <label for="drop-tier3-pct">下跌 3档 %</label>
                  <input class="tier-input" id="drop-tier3-pct" type="number" min="0.1" max="99" step="0.1" value="{drop_thresholds[2]}" required>
                </div>
                <div class="threshold-label">上涨</div>
                <div class="tier-field">
                  <label for="rise-tier1-pct">上涨 1档 %</label>
                  <input class="tier-input" id="rise-tier1-pct" type="number" min="0.1" max="99" step="0.1" value="{rise_thresholds[0]}" required>
                </div>
                <div class="tier-field">
                  <label for="rise-tier2-pct">上涨 2档 %</label>
                  <input class="tier-input" id="rise-tier2-pct" type="number" min="0.1" max="99" step="0.1" value="{rise_thresholds[1]}" required>
                </div>
                <div class="tier-field">
                  <label for="rise-tier3-pct">上涨 3档 %</label>
                  <input class="tier-input" id="rise-tier3-pct" type="number" min="0.1" max="99" step="0.1" value="{rise_thresholds[2]}" required>
                </div>
              </form>
            </div>
            <button class="watch-btn" type="submit" form="movement-settings-form">保存阈值</button>
          </div>
        </section>
      </div>
    </details>
    <section class="active-panel" data-panel="main">
      <h2>主观察</h2>
      <div class="cards">{symbol_cards}</div>
    </section>
    <section data-panel="market">
      <h2>大盘观察</h2>
      <div class="benchmark-cards">{benchmark_cards}</div>
    </section>
    <div class="overview" data-panel="activity">
      <section>
        <h2>今日运行概览</h2>
        <div class="stats">{metric_cards}</div>
      </section>
      <div class="cost-panel">
        <h2>AI 调用成本代理</h2>
        <div class="bar" aria-label="JSON 与图像调用比例"><i></i><i></i></div>
        <div class="legend"><span>JSON-only {json_count} 次</span><span>传图 {image_count} 次</span></div>
        <p class="sub">日常扫描优先 JSON，强候选才进入图像复核。</p>
      </div>
    </div>
    <div class="split" data-panel="activity">
      <section>
        <h2>最近执行时间线</h2>
        <table class="desktop-table"><thead><tr><th>时间</th><th>标的</th><th>级别</th><th>传图</th><th>方向</th><th>分数</th><th>置信度</th><th>视觉复核</th><th>错误</th></tr></thead><tbody>{llm_rows}</tbody></table>
        <div class="mobile-feed">{llm_mobile_cards}</div>
      </section>
      <aside class="errors">
        <h2>近期错误</h2>
        <ul>{error_items}</ul>
      </aside>
    </div>
    <section data-panel="activity">
      <h2>最近报警</h2>
      <table class="desktop-table"><thead><tr><th>时间</th><th>类型</th><th>标的</th><th>渠道</th><th>方向</th><th>分数</th><th>置信度</th></tr></thead><tbody>{alert_rows}</tbody></table>
      <div class="mobile-feed">{alert_mobile_cards}</div>
    </section>
  </main>
  <script>
    const countdownEl = document.getElementById("next-run-countdown");
    if (countdownEl) {{
      let remainingSeconds = Number.parseInt(countdownEl.dataset.seconds || "0", 10);
      const formatCountdown = (seconds) => {{
        if (!Number.isFinite(seconds) || seconds <= 0) {{
          return "即将执行";
        }}
        const h = Math.floor(seconds / 3600);
        const m = Math.floor((seconds % 3600) / 60);
        const s = seconds % 60;
        if (h > 0) {{
          return `${{h}}时${{m}}分${{s}}秒`;
        }}
        return `${{m}}分${{s}}秒`;
      }};
      countdownEl.textContent = formatCountdown(remainingSeconds);
      setInterval(() => {{
        remainingSeconds = Math.max(remainingSeconds - 1, 0);
        countdownEl.textContent = formatCountdown(remainingSeconds);
      }}, 1000);
    }}

    const watchForm = document.getElementById("watch-form");
    if (watchForm) {{
      watchForm.addEventListener("submit", async (event) => {{
        event.preventDefault();
        const input = document.getElementById("watch-symbol");
        const symbol = (input.value || "").trim().toUpperCase();
        if (!symbol) return;
        try {{
          const response = await fetch("/api/watchlist", {{
            method: "POST",
            headers: {{ "Content-Type": "application/json" }},
            body: JSON.stringify({{ symbol }}),
          }});
          if (!response.ok) {{
            const data = await response.json().catch(() => ({{}}));
            alert(data.detail || "加入失败，请检查代码格式");
            return;
          }}
          input.value = "";
          location.reload();
        }} catch (_) {{
          alert("网络异常，稍后再试");
        }}
      }});
    }}

    document.querySelectorAll("[data-remove-symbol]").forEach((button) => {{
      button.addEventListener("click", async () => {{
        const symbol = button.dataset.removeSymbol;
        if (!symbol) return;
        try {{
          const response = await fetch(`/api/watchlist/${{encodeURIComponent(symbol)}}`, {{
            method: "DELETE",
          }});
          if (!response.ok) {{
            const data = await response.json().catch(() => ({{}}));
            alert(data.detail || "移除失败，请稍后再试");
            return;
          }}
          location.reload();
        }} catch (_) {{
          alert("网络异常，稍后再试");
        }}
      }});
    }});

    const movementSettingsForm = document.getElementById("movement-settings-form");
    if (movementSettingsForm) {{
      movementSettingsForm.addEventListener("submit", async (event) => {{
        event.preventDefault();
        const readThresholds = (prefix) => [1, 2, 3].map((tier) => Number.parseFloat(document.getElementById(`${{prefix}}-tier${{tier}}-pct`).value));
        const fast_drop = readThresholds("drop");
        const fast_rise = readThresholds("rise");
        try {{
          const response = await fetch("/api/movement-alert-settings", {{
            method: "POST",
            headers: {{ "Content-Type": "application/json" }},
            body: JSON.stringify({{ fast_drop, fast_rise }}),
          }});
          if (!response.ok) {{
            const data = await response.json().catch(() => ({{}}));
            alert(data.detail || "保存失败，请检查三档阈值是否递增");
            return;
          }}
          location.reload();
        }} catch (_) {{
          alert("网络异常，稍后再试");
        }}
      }});
    }}

    const tabs = Array.from(document.querySelectorAll(".mobile-tab"));
    const panels = Array.from(document.querySelectorAll("[data-panel]"));
    const activateTab = (target) => {{
      tabs.forEach((tab) => tab.classList.toggle("active", tab.dataset.tabTarget === target));
      panels.forEach((panel) => panel.classList.toggle("active-panel", panel.dataset.panel === target));
      if (target === "settings") {{
        const drawer = document.querySelector(".settings-drawer");
        if (drawer) drawer.open = true;
      }}
    }};
    tabs.forEach((tab) => tab.addEventListener("click", () => activateTab(tab.dataset.tabTarget)));
  </script>
</body>
</html>
"""


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


def _render_benchmark_card(item: dict) -> str:
    return f"""
    <article class="benchmark-card">
      <div class="benchmark-head">
        <div>
          <b>{item['symbol']}</b>
          <div class="meta-row">
            <span class="pill bias-{item['bias'] or 'neutral'}">{_label(item['bias'])}</span>
            <span class="pill">{item['expected_move']}</span>
          </div>
        </div>
        <div class="score-wrap">
          <div class="score {'negative' if isinstance(item['sentiment_score'], (int, float)) and item['sentiment_score'] < 0 else ''}">{_fmt(item['regular_market_price'])}</div>
          <div class="confidence">{_fmt(item['regular_market_time'])}</div>
        </div>
      </div>
      <div class="benchmark-note">{item['outlook_note']}</div>
      <div class="benchmark-grid">
        <div class="cell"><span>常规盘区间</span>{item.get('day_range', '-')}</div>
        <div class="cell"><span>分数 / 置信度</span>{_fmt(item['sentiment_score'])} / {_fmt(item['confidence'])}</div>
        <div class="cell"><span>走弱触发</span>{_fmt(item['support_level'])}</div>
        <div class="cell"><span>走强触发</span>{_fmt(item['resistance_level'])}</div>
        <div class="cell"><span>RSI</span>{_fmt(item['rsi_14'])}</div>
        <div class="cell"><span>EMA20 / EMA50</span>{_fmt(item['ema_20'])} / {_fmt(item['ema_50'])}</div>
      </div>
      <details class="scenario-group">
        <summary>路径预期</summary>
        <div class="scenario-list">
          <div class="benchmark-note">{_fmt(item.get('upside_scenario'))}</div>
          <div class="benchmark-note">{_fmt(item.get('downside_scenario'))}</div>
          <div class="benchmark-note">{_fmt(item.get('rebound_scenario'))}</div>
        </div>
      </details>
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
