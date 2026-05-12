# Movement Alerts Design

## Goal

Add an independent movement alert stream for sudden price drops. It covers all enabled primary watchlist symbols plus SPY and QQQ, runs during overnight and premarket sessions, and sends Feishu messages that distinguish individual-stock movement from broad-market movement.

## Scope

- Covered symbols:
  - Primary symbols: enabled watchlist entries with profile `primary`, falling back to `SYMBOLS`.
  - Benchmark symbols: `BENCHMARK_SYMBOLS`, defaulting to SPY and QQQ.
- Trigger:
  - Pure price drop over recent 1 minute candles.
- Dashboard configuration:
  - Three drop thresholds stored in SQLite.
  - Tier 1 sends 1 notification, tier 2 sends 2 notifications, tier 3 sends 3 notifications.
- Delivery:
  - Feishu text messages.
- Persistence:
  - Save movement alerts into the existing `alerts` table with `type=movement_alert` and stable `event_key` values.

## Architecture

Add a focused `gemini_stock.rules.movement_alerts` module. It reads the latest 1 minute frame and the existing 15 minute technical snapshot, returns plain dataclass events, and knows nothing about webhooks.

`gemini_stock.main` calls this module after market data and the technical snapshot are saved, before AI analysis is required. A `maybe_send_movement_alerts` helper handles Feishu delivery, repeat counts, and database deduplication. Existing AI trade alerts and price-action alerts keep their current meanings.

SQLite stores the three user-editable thresholds in `movement_alert_settings`. The Dashboard exposes GET/POST APIs and a compact form so changes take effect on the worker's next scan without a restart.

## Trigger Logic

For version one, a movement alert fires when:

- There are at least 2 recent 1 minute candles.
- The latest close is below the highest close in the recent 10 candle window by at least the first configured threshold.
- The matching tier is the highest configured threshold crossed by the current drop.

Default thresholds are 1.2%, 2.0%, and 3.0%. No LLM call is needed for this alert stream.

## Message Shape

Individual-stock movement:

```text
【个股异动】TSLL｜跌幅 1档预警
----------------
10分钟 -1.62%｜现价 12.18｜高点 12.38
本档告警 1 次｜第 1 档
动作：先观察承接，不追空；等二次确认。
提示：仅研究提醒，注意仓位和大盘环境。
```

Benchmark movement:

```text
【大盘异动】SPY｜跌幅 1档预警
----------------
10分钟 -1.28%｜现价 512.40｜高点 519.05
本档告警 1 次｜第 1 档
动作：先看大盘承接，再判断个股信号。
提示：只看环境，不替代个股判断。
```

## Deduplication

The event key includes:

- Alert kind: `movement`.
- Symbol.
- Event type and tier: `fast_drop:tierN`.
- Trading date.
- Time bucket from the latest 1 minute candle, rounded down to 30 minutes.
- Repeat suffix: `n1`, `n2`, or `n3`.

This prevents the same tier from sending repeatedly while still allowing a higher tier or later time bucket to trigger.

## Error Handling

- Empty or short 1 minute frames return no alerts.
- Missing RSI, ATR, volume, or support levels do not matter because this stream is price-only.
- Feishu failures are logged and do not block the rest of the watchlist.

## Testing

Add tests for:

- A fast drop creates an individual-stock movement alert.
- A fast drop for SPY creates a broad-market movement alert with different copy.
- A deeper drop selects the highest matching tier and repeat count.
- Small moves do not alert.
- The main helper sends per tier repeat count, saves `movement_alert`, and skips duplicates.
- Dashboard can update the three configured thresholds.

