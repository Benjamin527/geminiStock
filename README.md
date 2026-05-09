# 美股 AI 盯盘与报警助手

这是一个针对 `CONL` / `TSLL` 的 AI 盯盘与报警原型，同时提供 `SPY` / `QQQ` 大盘观察。系统只做研究提醒，不做自动下单，不构成投资建议。

## 架构

- Python 负责事实层：OHLCV 获取、RSI、MACD、VWAP、ATR、EMA、支撑位、压力位计算。
- Gemini 负责解释层：综合新闻、技术快照和 K 线截图，输出结构化 JSON。
- Pydantic 负责约束层：所有 Gemini 输出必须通过 `GeminiSignal` schema 校验。
- 规则引擎负责报警层：LLM 不能直接决定最终报警，更不能下单。
- SQLite 负责审计层：保存 raw candles、features、news、llm outputs、alerts，便于复盘和回测。
- 可选 MySQL 同步层：本地交易系统继续写 SQLite，再增量同步到远程 MySQL，供服务器前台读取。

## 快速开始

```bash
cp .env.example .env
python3 -m venv .venv
.venv/bin/python -m pip install -U pip
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest -q
```

编辑 `.env`：

```bash
LLM_PROVIDER=auto
GEMINI_API_KEY=你的 Gemini API Key
GEMINI_MODEL=gemini-2.5-flash
OPENAI_API_KEY=你的 OpenAI-compatible API Key
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4o-mini
LLM_FALLBACK_ON_ERROR=true
DATA_PROVIDER=yfinance
POLYGON_API_KEY=
WORKER_STALE_AFTER_INTERVALS=2.5
MAX_SCHEDULER_SLEEP_SECONDS=300
BENCHMARK_SYMBOLS=["SPY","QQQ"]
DASHBOARD_DATA_SOURCE=sqlite
SYNC_REMOTE_MYSQL=false
REMOTE_MYSQL_HOST=
REMOTE_MYSQL_PORT=3306
REMOTE_MYSQL_USER=
REMOTE_MYSQL_PASSWORD=
REMOTE_MYSQL_DATABASE=gemini_stock
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
FEISHU_WEBHOOK_URL=
WECOM_WEBHOOK_URL=
```

运行一次：

```bash
RUN_ONCE=true .venv/bin/python -m gemini_stock.main
```

持续运行：

```bash
.venv/bin/python -m gemini_stock.main
```

## 调度策略

系统按美股交易时段自动决定是否运行，时间基于 `America/New_York`：

- 夜盘：20:00-04:00，yfinance 模式下不自动运行，避免因无新 K 线反复跳过或误用陈旧行情。
- 盘前：04:00-09:30，每 30 分钟运行一次。
- 盘中：09:30-16:00，每 20 分钟运行一次。
- 盘后：16:00-20:00，每 60 分钟运行一次。
- 休市：不自动执行，等待下一个夜盘或盘前时段。

调度会识别常见 NYSE 休市日和 13:00 提前收盘日；长等待会拆成最多 5 分钟的小睡眠，避免电脑睡眠 / Docker 恢复后长时间卡在旧倒计时里。

飞书通知有免打扰窗口：北京时间 23:00 到次日 09:00 不发送普通报警消息。每日复盘总结会在北京时间 08:00 后允许推送一次；系统仍会保存分析和报警判断记录，便于复盘。

Docker：

```bash
cp .env.example .env
docker compose up --build
```

## Web 控制台

本地只读控制台用于查看系统状态、最近 AI 分析、是否传图、技术事件、报警记录和已生成的复核图。它包含移动端优先的主观察卡片、大盘路径预期和手机卡片流，不提供下单、强制报警或修改策略的入口。控制台默认只读取后台已经落库的最近 1 分钟 K 线和技术快照，不在每次页面刷新时额外请求实时行情源，避免前台刷新造成 yfinance / Polygon 限流。

SPY / QQQ 大盘路径预期面向美股常规盘当天走势，展示常规盘预估区间、走强触发和走弱触发；夜盘只用于调度运行，不作为常规盘区间目标。

本地启动：

```bash
.venv/bin/python -m gemini_stock.web.app
```

打开：

```text
http://localhost:8000
```

Docker Compose 会同时启动 `gemini-stock` 和 `dashboard` 两个服务，控制台端口是 `8000`。

健康检查：

```bash
docker compose ps
docker compose exec dashboard python -m gemini_stock.healthcheck dashboard
docker compose exec gemini-stock python -m gemini_stock.healthcheck worker
```

控制台 `/api/health` 会返回后台是否 stale；`/api/status` 会展示后台状态、最近 AI 时间和本地 `.env` 敏感字段提醒。

如果前台要部署到服务器，建议使用：

```text
本地交易系统 -> 本地 SQLite -> 远程 MySQL
服务器前台 -> 读取远程 MySQL
```

服务器前台环境变量示例：

```bash
DASHBOARD_DATA_SOURCE=mysql
REMOTE_MYSQL_HOST=你的 MySQL 地址
REMOTE_MYSQL_PORT=3306
REMOTE_MYSQL_USER=你的用户名
REMOTE_MYSQL_PASSWORD=你的密码
REMOTE_MYSQL_DATABASE=gemini_stock
```

本地交易系统环境变量示例：

```bash
SYNC_REMOTE_MYSQL=true
REMOTE_MYSQL_HOST=你的 MySQL 地址
REMOTE_MYSQL_PORT=3306
REMOTE_MYSQL_USER=你的用户名
REMOTE_MYSQL_PASSWORD=你的密码
REMOTE_MYSQL_DATABASE=gemini_stock
```

## 报警规则

主标的 `CONL` / `TSLL` 以 AI 结构化输出的 `should_alert` 作为最终告警意图；本地规则只保留确定性硬拦截：

- `should_alert=false` 不发送报警
- 图像复核明确 `visual_confirmation=rejected` 不发送报警
- `price` 低于 `EMA50 - 2 * ATR` 不发送报警
- 同一 symbol 60 分钟内不重复报警

报警会被分成 `info`、`watch`、`strong` 三档写入数据库 payload。强买入和大盘急跌 / 趋势报警是 `strong`；普通 setup 是 `watch`；弱信号和 no-trade 相关提醒是 `info`。

当主标的同时满足强买入条件时，报警原因记录为 `buy_alert`：

- `sentiment_score > 7`
- `confidence > 0.65`
- `RSI < 35`
- `risk_reward_ratio >= 1.5`
- `setup_type != no_trade`

大盘观察 `SPY` / `QQQ` 使用独立规则：急跌、靠近关键支撑 / 压力位，或趋势分数达到阈值时报警。

主标的还会额外发送“价格到位提醒”：当最新 1 分钟价格进入 AI 给出的买入参考区、卖出参考区或回补买入区时，飞书会发送一条短消息，例如“最新价已到 xx，可考虑分批买入 / 卖出止盈”。同一交易日、同一标的、同一参考区间只发送一次，避免重复刷屏。

普通 AI 告警也会写入 `event_key`，并在数据库中检查同一事件和冷却窗口；即使 Docker 重启，也不会因为内存状态丢失而重复刷屏。同一根 15 分钟 K 线已经成功分析过时，后台会复用已有 AI 信号，不重复调用 LLM，但仍会继续用最新 1 分钟价格检查“价格到位提醒”。

如果在 `.env` 中配置持仓：

```bash
POSITIONS={"CONL":20,"TSLL":100}
AVERAGE_COSTS={"CONL":45.00,"TSLL":12.30}
```

价格到位提醒会附带当前持仓和成本，便于区分买入、加仓、减仓和止盈场景。

## 数据源说明

默认使用 yfinance 做原型。后台拉取 intraday 数据时会开启盘前 / 盘后数据，并在自动运行时段校验最新 K 线是否超过延迟容忍窗口；如果 yfinance 只返回上一交易日常规盘数据，系统会跳过本轮分析，避免用陈旧价格触发报警。控制台读取后台已保存的 K 线缓存，不会自行触发行情请求。yfinance 的 intraday 数据适合验证流程，但不建议作为生产盯盘数据源。

系统已经内置 Polygon-compatible 聚合行情源：

```bash
DATA_PROVIDER=polygon
POLYGON_API_KEY=你的 Polygon API Key
POLYGON_BASE_URL=https://api.polygon.io
```

也可以使用：

```bash
DATA_PROVIDER=auto
```

`auto` 会在配置 `POLYGON_API_KEY` 时优先使用 Polygon，否则回到 yfinance。

Docker 构建使用固定依赖版本，避免重新 build 后 yfinance、pandas 等关键库行为漂移。

## 信号复盘

项目提供 `gemini_stock.replay.summarize_alert_outcomes()`，可以按标的统计普通告警后的未来收益表现，例如 120 分钟后的平均收益和正收益比例，并按 `setup_type` 输出分组结果。

后台也会在每天北京时间 08:00 后，对上一自然日保存的 AI 建议和实际价格走势做一次复盘总结，并推送到飞书。复盘会给出命中数量、准确率、每个主标的的走势验证结果，以及学习总结；学习总结会持续强调 L1、二次握手、失效条件、不追价区和仓位约束，避免后续建议变成无条件追涨杀跌。

示例：

```python
from gemini_stock.replay import summarize_alert_outcomes
from gemini_stock.storage.db import Database

summary = summarize_alert_outcomes(Database("data/gemini_stock.sqlite"), ["CONL", "TSLL"], horizon_minutes=120)
print(summary["by_setup_type"])
```

## 新闻层

当前 MVP 提供 `NewsProvider` 抽象和空新闻源，包含新闻去重、HTML 清洗、时间戳标准化和 ticker relevance score 的基础工具。接入真实新闻 API 时只需要实现 `NewsProvider.get_news()` 并返回 `NewsItem`。

## 通知

推荐使用飞书群机器人：

1. 飞书群聊里添加自定义机器人。
2. 复制 Webhook 地址。
3. 写入 `.env` 的 `FEISHU_WEBHOOK_URL`。

当前实现使用飞书普通文本消息：

```json
{"msg_type": "text", "content": {"text": "..."}}
```

如果你的飞书机器人开启了签名校验，需要后续再加 `FEISHU_WEBHOOK_SECRET` 签名逻辑。Telegram 仍然可用，企业微信字段保留但不推荐作为默认通知渠道。

## 配置安全

`.env` 不应提交到 Git。控制台会检查本地 `.env` 中是否包含看起来像真实值的 API Key、Webhook 和数据库密码，并在状态区提示字段名。已经暴露过的 Key 建议在对应平台轮换。

## 安全边界

- 系统只用于研究和提醒，不构成投资建议。
- 不包含任何自动下单逻辑。
- Gemini 不计算技术指标，只读取 Python 计算出的 `technical_snapshot`。
- Gemini / OpenAI-compatible 返回非法 JSON 或请求失败时会自动重试；如果 `LLM_FALLBACK_ON_ERROR=true`，系统会降级到本地规则信号，避免整轮扫描被 LLM 阻塞。降级信号是保守的观察信号，不会触发交易告警。
