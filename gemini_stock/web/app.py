from __future__ import annotations

import re
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from gemini_stock.config import load_settings
from gemini_stock.rules.trade_plan import has_actionable_trade_levels
from gemini_stock.storage.db import Database
from gemini_stock.web.mysql_repository import MySQLDashboardRepository
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
    repo = MySQLDashboardRepository(settings, charts) if settings.dashboard_data_source == "mysql" else DashboardRepository(db_path, charts)
    db = Database(db_path) if settings.dashboard_data_source != "mysql" else None
    if db is not None:
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
            alerts=repo.get_recent_alerts(symbols_now),
            errors=repo.get_recent_errors(symbols_now),
            watchlist=watchlist_payload(),
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
            "recent_alerts": repo.get_recent_alerts(symbols_now),
            "recent_errors": repo.get_recent_errors(symbols_now),
            "watchlist": watchlist_payload(),
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

    @app.post("/api/watchlist")
    def api_add_watch_symbol(payload: dict) -> dict:
        if db is None:
            raise HTTPException(status_code=400, detail="watchlist editing is unavailable when dashboard uses mysql mode")
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


def render_dashboard(
    status: dict,
    metrics: dict,
    symbols: list[dict],
    benchmarks: list[dict],
    llm_outputs: list[dict],
    alerts: list[dict],
    errors: list[dict],
    watchlist: dict[str, list[str]],
) -> str:
    symbol_cards = "\n".join(_render_symbol_card(item) for item in symbols)
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
          <td>{row['created_at_utc']}</td><td>{row['symbol']}</td><td>{row['channel']}</td>
          <td>{_label(row['bias'])}</td><td>{_fmt(row['sentiment_score'])}</td><td>{_fmt(row['confidence'])}</td>
        </tr>
        """
        for row in alerts
    ) or "<tr><td colspan='6'>暂无报警记录</td></tr>"
    error_items = "\n".join(
        f"<li><b>{row['symbol']}</b><span>{row['created_at_utc']} · {_label(row['analysis_level'])}</span><p>{row['error']}</p></li>"
        for row in errors
    ) or "<li class='muted-row'>暂无近期错误</li>"
    monitored_primary = " · ".join(watchlist.get("primary") or [])
    monitored_benchmark = " · ".join(watchlist.get("benchmark") or [])
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
    .watch-badge {{ border: 1px solid var(--line); border-radius: 999px; padding: 4px 10px; font-size: 12px; color: var(--ink); background: rgba(102,183,255,.08); }}
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
    main {{ padding: 24px 32px 40px; display: grid; gap: 18px; }}
    .status {{ display: grid; grid-template-columns: 1.2fr 1fr 1fr 1.4fr; gap: 12px; }}
    .metric, section, .card, .stat, .cost-panel, .errors {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 14px; box-shadow: 0 10px 30px rgba(4,9,14,.2); }}
    .metric.primary {{ background: var(--dark); color: #f6f8f4; border-color: var(--line); }}
    .metric b {{ display: block; font-size: clamp(18px, 3vw, 26px); margin-top: 3px; line-height: 1.15; }}
    .metric span, .stat span {{ color: var(--muted); display: block; font-size: 12px; }}
    .metric.primary span {{ color: #b9c4bd; }}
    .overview {{ display: grid; grid-template-columns: minmax(0, 2fr) minmax(280px, 1fr); gap: 12px; }}
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
    .errors ul {{ list-style: none; padding: 0; margin: 0; display: grid; gap: 10px; }}
    .errors li {{ border-top: 1px solid var(--line); padding-top: 10px; }}
    .errors li:first-child {{ border-top: 0; padding-top: 0; }}
    .errors span {{ display: block; color: var(--muted); font-size: 12px; }}
    .errors p {{ margin: 4px 0 0; color: var(--red); overflow-wrap: anywhere; }}
    .muted-row {{ color: var(--muted); }}
    @media (max-width: 980px) {{
      .status, .overview, .split {{ grid-template-columns: 1fr; }}
      .stats {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .watch-panel {{ grid-template-columns: 1fr; }}
      .watch-form {{ justify-content: flex-start; }}
      header, main {{ padding-left: 16px; padding-right: 16px; }}
      table {{ display: block; overflow-x: auto; white-space: nowrap; }}
    }}
    @media (max-width: 680px) {{
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
    <div class="status">
      <div class="metric primary"><span>距离下次执行</span><b>{status['countdown_label']}</b></div>
      <div class="metric"><span>市场阶段</span><b>{_label(status['market_session'])}</b></div>
      <div class="metric"><span>当前是否执行</span><b>{'是' if status['should_run_now'] else '否'}</b></div>
      <div class="metric"><span>后台状态</span><b>{worker_state}</b></div>
    </div>
    <div class="sub">{'配置提醒：本地 .env 含敏感字段 ' + warning_text + '，建议轮换并移入密钥管理。' if warning_text else ''}</div>
    <section>
      <h2>监控列表</h2>
      <div class="watch-panel">
        <div class="watch-meta">
          <div class="sub">主监控：{monitored_primary or '-'}</div>
          <div class="sub">参考监控：{monitored_benchmark or '-'}</div>
          <div class="watch-badges">{''.join(f"<span class='watch-badge'>{symbol}</span>" for symbol in watchlist.get('primary', [])) or "<span class='watch-badge'>暂无</span>"}</div>
        </div>
        <form class="watch-form" id="watch-form">
          <input class="watch-input" id="watch-symbol" name="symbol" placeholder="输入代码，如 NVDA" maxlength="10" required>
          <button class="watch-btn" type="submit">加入监控</button>
        </form>
      </div>
    </section>
    <section>
      <h2>主观察</h2>
      <div class="cards">{symbol_cards}</div>
    </section>
    <section>
      <h2>大盘观察</h2>
      <div class="benchmark-cards">{benchmark_cards}</div>
    </section>
    <div class="overview">
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
    <div class="split">
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
    <section>
      <h2>最近报警</h2>
      <table class="desktop-table"><thead><tr><th>时间</th><th>标的</th><th>渠道</th><th>方向</th><th>分数</th><th>置信度</th></tr></thead><tbody>{alert_rows}</tbody></table>
      <div class="mobile-feed">{alert_mobile_cards}</div>
    </section>
  </main>
  <script>
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
    action_label = _action_label(item["bias"])
    action_hint = _action_hint(item["bias"])
    return f"""
    <article class="card">
      <div class="card-head">
        <div class="headline">
          <div class="symbol">{item['symbol']}</div>
          <div class="meta-row">
            <span class="pill bias-{bias}">{_label(item['bias'])}</span>
            <span class="pill">{_label(item['setup_type'])}</span>
            <span class="pill">{_label(item['analysis_level'])}</span>
          </div>
        </div>
        <div class="score-wrap">
          <div class="{score_class}">{_fmt(score_value)}</div>
          <div class="confidence">置信度 {_fmt(item['confidence'])}</div>
        </div>
      </div>
      <div class="action-banner">
        <div class="action-copy">
          <span>建议动作</span>
          <b>{action_hint}</b>
        </div>
        <div class="action-tag {bias}">{action_label}</div>
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
      <div class="feed-meta">{row['channel']} · {_label(row['setup_type'])}</div>
      <div>分数 {_fmt(row['sentiment_score'])} · 置信度 {_fmt(row['confidence'])}</div>
    </div>
    """


app = create_app()


def main() -> None:
    import uvicorn

    uvicorn.run("gemini_stock.web.app:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    main()
