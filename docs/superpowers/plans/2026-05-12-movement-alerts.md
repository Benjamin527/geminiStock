# Movement Alerts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an independent Feishu movement-alert stream for price-only fast drops across primary watchlist symbols and SPY/QQQ.

**Architecture:** Add a pure rule module that turns 1 minute candles and 15 minute snapshots into movement events. Store three user-editable thresholds in SQLite, expose them in the Dashboard, and wire alerts through `gemini_stock.main` with existing Feishu sending and alert persistence.

**Tech Stack:** Python, pandas, pytest, existing SQLite `alerts` table, FastAPI Dashboard, existing Feishu webhook helpers.

---

## File Structure

- Create `gemini_stock/rules/movement_alerts.py`: dataclass event model, fast-drop detection, tier selection, event-key generation, text formatting.
- Add `tests/test_movement_alerts.py`: unit tests for detection, tiering, and formatting.
- Modify `gemini_stock/storage/db.py`: threshold persistence with defaults and validation.
- Add `tests/test_movement_alert_settings.py`: database settings tests.
- Modify `gemini_stock/main.py`: call movement-alert helper after candles and snapshots are available; send and persist per-tier repeats.
- Add `tests/test_movement_alert_integration.py`: Feishu send, persistence, repeat-count, and deduplication tests.
- Modify `gemini_stock/web/app.py`: threshold API and Dashboard form.
- Modify `gemini_stock/schedule.py`: run overnight and premarket every 5 minutes.
- Modify `README.md`: document scheduling and movement alert behavior.

### Task 1: Rule Module

**Files:**
- Create: `gemini_stock/rules/movement_alerts.py`
- Test: `tests/test_movement_alerts.py`

- [x] **Step 1: Write failing tests**

```python
def test_fast_drop_near_support_creates_primary_movement_alert():
    ...

def test_fast_drop_for_benchmark_uses_market_label():
    ...

def test_deeper_drop_uses_highest_matching_tier_and_repeat_count():
    ...

def test_small_move_does_not_create_alert():
    ...
```

- [x] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_movement_alerts.py -q`

Expected: fails because `gemini_stock.rules.movement_alerts` does not exist.

- [x] **Step 3: Implement module**

Create a `MovementAlert` dataclass with `symbol`, `profile`, `event_type`, `tier`, `repeat_count`, `latest_price`, `window_minutes`, `drop_pct`, `peak_price`, `event_key`, and `timestamp_utc`.

Implement `build_movement_alerts(technical_snapshot, candles_1m, profile, trading_date, threshold_pcts)` and `format_movement_alert(alert)`.

- [x] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_movement_alerts.py -q`

Expected: all tests pass.

### Task 2: Persistence And Main Wiring

**Files:**
- Modify: `gemini_stock/storage/db.py`
- Modify: `gemini_stock/main.py`
- Test: `tests/test_movement_alert_settings.py`
- Test: `tests/test_movement_alert_integration.py`

- [x] **Step 1: Write failing settings and integration tests**

```python
def test_database_returns_default_movement_alert_thresholds():
    ...

def test_maybe_send_movement_alerts_sends_and_deduplicates_feishu():
    ...

def test_maybe_send_movement_alerts_repeats_by_tier():
    ...
```

- [x] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_movement_alert_settings.py tests/test_movement_alert_integration.py -q`

Expected: fails because settings and `maybe_send_movement_alerts` are not defined.

- [x] **Step 3: Implement persistence and wiring**

Create `movement_alert_settings`, add `get_movement_alert_thresholds()` and `save_movement_alert_thresholds()`, import movement alert helpers in `gemini_stock.main`, call `maybe_send_movement_alerts` after candles and the technical snapshot exist, and save sent alerts with `type=movement_alert`.

- [x] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_movement_alert_settings.py tests/test_movement_alert_integration.py -q`

Expected: all tests pass.

### Task 3: Dashboard, Schedule, Docs

**Files:**
- Modify: `gemini_stock/web/app.py`
- Modify: `gemini_stock/web/repository.py`
- Modify: `gemini_stock/web/mysql_repository.py`
- Modify: `gemini_stock/schedule.py`
- Modify: `README.md`

- [x] **Step 1: Add Dashboard and schedule tests**

```python
def test_dashboard_can_update_movement_alert_thresholds():
    ...

def test_premarket_runs_every_thirty_minutes():
    ...

def test_overnight_runs_every_five_minutes_from_sunday_evening():
    ...
```

- [x] **Step 2: Implement UI/API and schedule changes**

Add `/api/movement-alert-settings`, render the three threshold inputs, and make premarket/overnight sessions return a 5 minute runnable schedule.

- [x] **Step 3: Run focused test suite**

Run: `.venv/bin/python -m pytest tests/test_movement_alerts.py tests/test_movement_alert_integration.py tests/test_movement_alert_settings.py tests/test_schedule.py tests/test_web_dashboard.py::test_dashboard_can_update_movement_alert_thresholds -q`

Expected: all tests pass.

