# Guance ddtrace Tracing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable Guance-compatible `ddtrace` tracing for both Docker containers and add small manual worker spans so each scan cycle appears as a coherent trace.

**Architecture:** Use `ddtrace-run` at the container boundary for both services, keep service names explicit per container through environment variables, and add a thin tracing wrapper in the worker runtime. The dashboard relies on auto-instrumentation while the worker gets a small manual span hierarchy around each cycle and symbol run.

**Tech Stack:** Python 3.13, Docker Compose, ddtrace, FastAPI, pytest

---

### Task 1: Lock tracing behavior with failing tests

**Files:**
- Create: `tests/test_tracing.py`
- Test: `tests/test_tracing.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_run_once_trace_wraps_cycle_and_children(...):
    ...

def test_run_symbol_trace_sets_symbol_tags_and_propagates_failures(...):
    ...

def test_docker_runtime_uses_ddtrace_run_and_distinct_service_names(...):
    ...
```

- [ ] **Step 2: Run the tracing tests to verify they fail**

Run: `.venv/bin/pytest tests/test_tracing.py -q`
Expected: FAIL because tracing helpers and runtime wiring do not exist yet.

- [ ] **Step 3: Do not commit the red test state**

Keep the red state local in this session.

### Task 2: Add runtime dependency and Docker wiring

**Files:**
- Modify: `pyproject.toml`
- Modify: `.env.example`
- Modify: `docker-compose.yml`
- Modify: `README.md`
- Test: `tests/test_tracing.py`

- [ ] **Step 1: Add `ddtrace` runtime dependency**

Pin the dependency in `pyproject.toml` using the selected tested version.

- [ ] **Step 2: Add default tracing environment variables**

Document and expose:

```bash
DD_AGENT_HOST=121.196.154.93
DD_AGENT_PORT=9529
DD_TRACE_AGENT_PORT=9529
DD_ENV=local
DD_LOGS_INJECTION=true
DD_SERVICE=gemini-worker
DD_DASHBOARD_SERVICE=gemini-dashboard
```

- [ ] **Step 3: Update compose commands**

Use:

```yaml
command: ["ddtrace-run", "python", "-m", "gemini_stock.main"]
command: ["ddtrace-run", "python", "-m", "gemini_stock.web.app"]
```

and override `DD_SERVICE` for the dashboard service to `gemini-dashboard`.

- [ ] **Step 4: Run the tracing tests**

Run: `.venv/bin/pytest tests/test_tracing.py -q`
Expected: still FAIL, but only for missing worker tracing behavior.

### Task 3: Add worker tracing wrapper

**Files:**
- Modify: `gemini_stock/main.py`
- Test: `tests/test_tracing.py`

- [ ] **Step 1: Add a small tracer abstraction**

Create a helper that safely returns a tracer span context when `ddtrace` is available and otherwise behaves as a no-op.

- [ ] **Step 2: Wrap `run_once()` in a root span**

Use a root span named `worker.run_once` and add tags for:

```python
session
data_provider
llm_provider
symbol_count
benchmark_symbol_count
```

- [ ] **Step 3: Wrap `run_symbol()` in child spans**

Use child spans named `worker.run_symbol` and tag:

```python
symbol
profile
```

Keep exception behavior unchanged so failed symbol runs still bubble the same way they do now.

- [ ] **Step 4: Wrap movement-only and maintenance helpers**

Use:

```python
worker.run_movement_only
worker.maintenance
```

only around the existing helper calls, with no change to business logic.

- [ ] **Step 5: Run the tracing tests**

Run: `.venv/bin/pytest tests/test_tracing.py -q`
Expected: PASS

### Task 4: Verify the broader system

**Files:**
- Test: `tests/test_tracing.py`
- Test: `tests/test_duplicate_analysis_skip.py`
- Test: `tests/test_premarket_brief.py`
- Test: `tests/test_web_dashboard.py`

- [ ] **Step 1: Run focused regression tests**

Run: `.venv/bin/pytest tests/test_tracing.py tests/test_duplicate_analysis_skip.py tests/test_premarket_brief.py tests/test_web_dashboard.py -q`
Expected: PASS

- [ ] **Step 2: Run the full test suite**

Run: `.venv/bin/pytest -q`
Expected: PASS

- [ ] **Step 3: Commit the implementation**

```bash
git add pyproject.toml .env.example docker-compose.yml README.md gemini_stock/main.py tests/test_tracing.py docs/superpowers/plans/2026-05-12-guance-ddtrace.md
git commit -m "Add Guance ddtrace tracing"
```
