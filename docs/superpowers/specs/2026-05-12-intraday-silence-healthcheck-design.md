# Intraday Silence Health Check Design

## Goal

Add a lightweight self-check notification during the U.S. regular session opening window so the system can warn when it appears unhealthy even though no alerts have been sent recently.

This design is intentionally narrow. It should not create periodic "all good" messages. It should only send a notification when the service has been quiet for 30 minutes and the quiet period appears abnormal.

## Current Behavior

- The worker already runs automatically during the regular session.
- From 09:30 to 11:00 America/New_York, it runs every 1 minute.
- After 11:00 until the regular close, it runs every 30 minutes.
- Existing health logic marks the worker stale based on the latest `llm_outputs` timestamp, but it does not notify when alert delivery becomes suspiciously quiet.

## Scope

- Session scope:
  - Only during `MarketSession.REGULAR`.
  - Only from 09:30 to 11:00 America/New_York.
- Silence window:
  - Trigger evaluation when no alert has been persisted in the last 30 minutes.
- Delivery:
  - Feishu only, reusing the existing quiet-hours bypass behavior already used by important operational notifications.
- Persistence:
  - Save the self-check notification into `alerts` with a dedicated `type`.
- Out of scope:
  - No "system healthy" heartbeat messages.
  - No new dashboard control surface in version one.
  - No changes to the existing alert logic or trading rules.

## Proposed Behavior

On each worker run, after the normal symbol processing and maintenance tasks finish, evaluate whether an opening-session self-check is needed.

The self-check sends a Feishu message only when both conditions are true:

1. There has been no saved alert in the last 30 minutes.
2. The system detects at least one abnormal condition.

If there were recent alerts, do nothing. Quietness alone is not treated as a problem unless paired with an abnormal condition.

## Abnormal Conditions

Version one should notify when any of these checks fail:

- Worker stale:
  - The latest `llm_outputs.created_at_utc` is older than the current stale threshold derived from the active schedule decision.
- Primary watchlist market data stale:
  - At least one enabled primary symbol has a latest saved `1m` candle older than `DELAYED_DATA_TOLERANCE_MINUTES`.
- No recent market data for primary symbols:
  - At least one enabled primary symbol has no saved `1m` candle at all.

The message should include enough detail to explain why the self-check fired, for example which symbols are stale and how old the last worker output is.

## Deduplication

The self-check should send at most once per 30-minute bucket.

Event key shape:

```text
self_check:opening_silence:YYYY-MM-DD:HHMM
```

Where `HHMM` is the New York time rounded down to the current 30-minute bucket. This keeps the notification operationally useful without repeating on every 1-minute run.

## Message Shape

Example:

```text
【运行自检】开盘静默 30 分钟
----------------
时间：2026-05-12 10:00 ET
结论：发现异常，近 30 分钟没有任何推送
Worker：最新分析结果距今 17 分钟
行情：CONL 1m 延迟 41 分钟；TSLL 1m 延迟 39 分钟
提示：请检查行情源、容器运行状态和通知链路。
```

Use an interactive Feishu card so the operational message matches the rest of the current notification style.

## Architecture

Keep the implementation close to the current runtime flow:

- Add a small self-check helper in `gemini_stock.main`.
- Add database read helpers in `gemini_stock.storage.db` for:
  - recent alerts within a lookback window
  - latest worker output timestamp
  - latest saved `1m` candle timestamp by symbol
- Reuse `evaluate_worker_health()` for the worker-stale part instead of duplicating stale logic.
- Reuse the existing primary watchlist resolution:
  - enabled `watchlist` entries with profile `primary`
  - fallback to `settings.symbols`

This keeps the feature operational and low-risk without introducing a new subsystem.

## Trigger Placement

Call the self-check once per `run_once()` execution after:

- all primary and benchmark symbol runs
- movement-only symbol runs
- maintenance tasks

This placement ensures the self-check sees the freshest possible writes from the current cycle before deciding whether the system has been unusually quiet.

## Testing

Add tests for:

- regular-session opening window runs every minute already and remains unchanged
- self-check does not send outside 09:30-11:00 ET
- self-check does not send when recent alerts exist
- self-check does not send when the system is healthy but quiet
- self-check sends when no recent alerts exist and worker health is stale
- self-check sends when no recent alerts exist and primary symbols have stale or missing `1m` candles
- self-check deduplicates within the same 30-minute bucket

## Risks and Tradeoffs

- Quiet markets can legitimately produce no alerts for 30 minutes. This design avoids false positives by requiring an abnormal condition in addition to silence.
- Because the signal depends on saved alerts, a failure before alert persistence could still look like silence. That is acceptable for version one because the self-check is meant to surface suspicious quiet periods, not prove full end-to-end correctness.
- Reusing the existing alert table keeps rollout simple, but operational self-check messages will appear in recent alerts views. That is acceptable if they use a distinct `type`, such as `system_self_check`.
