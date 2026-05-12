# Intraday Silence Health Check Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opening-session self-check that sends a Feishu alert only when the system has been silent for 30 minutes and the silence coincides with an abnormal runtime condition.

**Architecture:** Keep the feature inside the existing worker flow. Add narrow database read helpers, a self-check helper in `gemini_stock.main`, and one notification formatter in the notifier module. Reuse existing schedule and worker-health logic so the new behavior stays consistent with the rest of the runtime.

**Tech Stack:** Python 3.13, SQLite, FastAPI project utilities, pytest

---

### Task 1: Capture self-check behavior with failing tests

**Files:**
- Modify: `tests/test_premarket_brief.py`
- Test: `tests/test_premarket_brief.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_opening_silence_self_check_skips_outside_opening_window(...):
    ...

def test_opening_silence_self_check_skips_when_recent_alert_exists(...):
    ...

def test_opening_silence_self_check_skips_when_system_is_healthy(...):
    ...

def test_opening_silence_self_check_sends_when_worker_or_market_data_is_unhealthy(...):
    ...

def test_opening_silence_self_check_deduplicates_within_bucket(...):
    ...
```

- [ ] **Step 2: Run the targeted tests to verify they fail**

Run: `.venv/bin/pytest tests/test_premarket_brief.py -q`
Expected: FAIL with missing helper behavior for the new opening-session self-check.

- [ ] **Step 3: Commit the red test state only if requested**

Do not commit the red state by default in this session.

### Task 2: Add database read helpers for silence detection

**Files:**
- Modify: `gemini_stock/storage/db.py`
- Test: `tests/test_premarket_brief.py`

- [ ] **Step 1: Add minimal read helpers**

```python
def has_any_recent_alert(self, now: datetime, within_minutes: int) -> bool: ...
def get_latest_llm_output_time(self) -> str | None: ...
def get_latest_candle_time(self, symbol: str, interval: str) -> datetime | None: ...
```

- [ ] **Step 2: Keep all timestamps normalized to UTC**

Use the existing internal datetime parsing helpers instead of inventing a second timestamp parser.

- [ ] **Step 3: Run the targeted tests**

Run: `.venv/bin/pytest tests/test_premarket_brief.py -q`
Expected: still FAIL, but now only for missing self-check runtime behavior.

### Task 3: Implement the opening-session silence self-check

**Files:**
- Modify: `gemini_stock/main.py`
- Modify: `gemini_stock/notify/channels.py`
- Test: `tests/test_premarket_brief.py`
- Test: `tests/test_schedule.py`

- [ ] **Step 1: Add self-check message builders**

```python
def build_opening_silence_self_check_text(...): ...
def build_opening_silence_self_check_card(...): ...
```

- [ ] **Step 2: Add a runtime helper in `gemini_stock.main`**

```python
def maybe_send_opening_silence_self_check(
    settings: Settings,
    db: Database,
    primary_symbols: list[str],
    now: datetime | None = None,
) -> bool: ...
```

The helper should:
- return early outside `MarketSession.REGULAR`
- return early outside `09:30-11:00` New York time
- return early when any alert exists in the last 30 minutes
- evaluate worker staleness with `evaluate_worker_health()`
- inspect latest `1m` candle freshness for primary symbols
- send one Feishu interactive card only when at least one abnormal condition exists
- save the alert with `type="system_self_check"`
- deduplicate by a 30-minute New York time bucket

- [ ] **Step 3: Call the helper from `run_once()`**

Place it after:
- primary and benchmark symbol processing
- movement-only symbol processing
- maintenance tasks

- [ ] **Step 4: Run the targeted tests**

Run: `.venv/bin/pytest tests/test_premarket_brief.py tests/test_schedule.py -q`
Expected: PASS

### Task 4: Run broader verification

**Files:**
- Test: `tests/test_premarket_brief.py`
- Test: `tests/test_schedule.py`
- Test: `tests/test_movement_alert_integration.py`
- Test: `tests/test_web_dashboard.py`

- [ ] **Step 1: Run related regression tests**

Run: `.venv/bin/pytest tests/test_premarket_brief.py tests/test_schedule.py tests/test_movement_alert_integration.py tests/test_web_dashboard.py -q`
Expected: PASS

- [ ] **Step 2: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: PASS

- [ ] **Step 3: Commit the implementation**

```bash
git add gemini_stock/main.py gemini_stock/notify/channels.py gemini_stock/storage/db.py tests/test_premarket_brief.py tests/test_schedule.py docs/superpowers/plans/2026-05-12-intraday-silence-healthcheck.md
git commit -m "Add intraday silence self-check"
```
