# Market Monitoring Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the market monitoring workflow more reliable, less noisy, and more actionable by improving provider selection, alert semantics, scheduling, and dashboard prioritization.

**Architecture:** Keep the current worker-dashboard split, but tighten the monitoring loop in four layers: provider selection and freshness visibility, event-scoped alert semantics, session-aware scheduling, and dashboard ranking views. The work stays incremental and compatible with the current SQLite-backed runtime so we can ship in small, testable slices.

**Tech Stack:** Python 3.13, FastAPI, SQLite, pytest, Polygon, yfinance

---

### Task 1: Prefer Stable Market Data For Runtime Monitoring

**Files:**
- Modify: `gemini_stock/config.py`
- Modify: `gemini_stock/data/provider_factory.py`
- Modify: `gemini_stock/main.py`
- Modify: `gemini_stock/web/repository.py`
- Modify: `README.md`
- Test: `tests/test_market_data_factory.py`
- Test: `tests/test_web_dashboard.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_market_data_factory_auto_prefers_polygon_when_key_is_configured():
    provider = create_market_data_provider(Settings(data_provider="auto", polygon_api_key="test-key"))
    assert isinstance(provider, PolygonMarketDataProvider)


def test_dashboard_symbol_state_exposes_price_source_and_freshness(tmp_path, monkeypatch):
    repo = DashboardRepository(tmp_path / "signals.db", tmp_path)
    assert repo.get_symbol_states(["TSLL"])[0]["price_source"] in {"stored_1m_candle", "feature_snapshot", None}
```

- [ ] **Step 2: Run tests to verify the current behavior baseline**

Run: `./.venv/bin/pytest -q tests/test_market_data_factory.py tests/test_web_dashboard.py`
Expected: Existing tests pass; add the new expectations and watch at least one test fail before implementation.

- [ ] **Step 3: Make runtime provider preference explicit**

```python
class Settings(BaseSettings):
    data_provider: Literal["auto", "yfinance", "polygon"] = "auto"
```

```python
def create_market_data_provider(settings: Settings) -> MarketDataProvider:
    if settings.data_provider == "polygon":
        ...
    if settings.data_provider == "auto" and settings.polygon_api_key:
        return PolygonMarketDataProvider(...)
    return YFinanceMarketDataProvider(settings.delayed_data_tolerance_minutes)
```

```python
class RuntimeContext:
    @classmethod
    def from_settings(cls, settings: Settings) -> RuntimeContext:
        return cls(
            data_provider=create_market_data_provider(settings),
            ...
        )
```

- [ ] **Step 4: Surface data-source state clearly in the dashboard**

```python
states.append(
    {
        "symbol": symbol,
        ...
        "price_source": quote.get("source") or ("feature_snapshot" if feature_payload else None),
        "data_freshness": _freshness_state(latest_1m["timestamp_utc"] if latest_1m else None),
    }
)
```

```md
默认 `DATA_PROVIDER=auto`，配置 `POLYGON_API_KEY` 时优先使用 Polygon 作为盘中监控源；未配置时自动回落到 yfinance。
```

- [ ] **Step 5: Run tests to verify the slice passes**

Run: `./.venv/bin/pytest -q tests/test_market_data_factory.py tests/test_web_dashboard.py`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add gemini_stock/config.py gemini_stock/data/provider_factory.py gemini_stock/main.py gemini_stock/web/repository.py README.md tests/test_market_data_factory.py tests/test_web_dashboard.py
git commit -m "feat: prefer stable runtime market data source"
```

### Task 2: Upgrade Alert Cooldown From Symbol-Level To Event-Level

**Files:**
- Modify: `gemini_stock/rules/alert_rules.py`
- Modify: `gemini_stock/main.py`
- Modify: `tests/test_schemas_and_rules.py`
- Test: `tests/test_price_action_integration.py`
- Test: `tests/test_movement_alert_integration.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_primary_rule_engine_cooldown_is_scoped_to_reason_and_symbol():
    engine = AlertRuleEngine(cooldown_minutes=60)
    first = engine.evaluate(_signal(symbol="TSLL", should_alert=True), _snapshot(symbol="TSLL"))
    second = engine.evaluate(_signal(symbol="TSLL", should_alert=True, setup_type="breakdown"), _snapshot(symbol="TSLL"))
    assert first.should_alert is True
    assert second.reason != "cooldown_active"
```

```python
def test_benchmark_rule_engine_cooldown_does_not_suppress_different_trigger_family():
    engine = BenchmarkAlertRuleEngine(cooldown_minutes=60)
    ...
```

- [ ] **Step 2: Run tests to verify they fail for the current symbol-only cooldown**

Run: `./.venv/bin/pytest -q tests/test_schemas_and_rules.py`
Expected: FAIL on the new cooldown expectations

- [ ] **Step 3: Replace symbol-only cooldown keys with event-scoped keys**

```python
class AlertRuleEngine:
    def __init__(self, cooldown_minutes: int = 60) -> None:
        self.cooldown = timedelta(minutes=cooldown_minutes)
        self._last_alert_at: dict[str, datetime] = {}

    def _cooldown_key(self, symbol: str, reason: str, signal: GeminiSignal) -> str:
        return ":".join([symbol, reason, signal.bias or "-", signal.setup_type or "-"])
```

```python
reason = "buy_alert" if _is_strong_buy_signal(signal, technical_snapshot) else "llm_alert"
cooldown_key = self._cooldown_key(symbol, reason, signal)
last_alert = self._last_alert_at.get(cooldown_key)
...
self._last_alert_at[cooldown_key] = current_time
```

```python
def should_send_decision_alert(...):
    alert_type = "benchmark_alert" if str(decision.reason).startswith("benchmark_") else "primary_alert"
    return not db.has_recent_alert_type(...)
```

- [ ] **Step 4: Keep integration deduplication behavior intact**

```python
payload["event_key"] = build_decision_alert_event_key(decision, alert_trading_date)
```

Make sure database-backed event dedup still prevents the same alert from sending twice, while the in-memory cooldown no longer suppresses a different alert family on the same symbol.

- [ ] **Step 5: Run tests to verify the cooldown semantics**

Run: `./.venv/bin/pytest -q tests/test_schemas_and_rules.py tests/test_price_action_integration.py tests/test_movement_alert_integration.py`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add gemini_stock/rules/alert_rules.py gemini_stock/main.py tests/test_schemas_and_rules.py tests/test_price_action_integration.py tests/test_movement_alert_integration.py
git commit -m "feat: scope alert cooldowns by event"
```

### Task 3: Make Movement Alerts Require More Context

**Files:**
- Modify: `gemini_stock/rules/movement_alerts.py`
- Modify: `gemini_stock/main.py`
- Modify: `gemini_stock/storage/db.py`
- Modify: `gemini_stock/web/repository.py`
- Modify: `tests/test_movement_alerts.py`
- Modify: `tests/test_movement_alert_integration.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_fast_drop_without_volume_confirmation_does_not_alert():
    alerts = build_movement_alerts("TSLL", _candles_without_volume_expansion(), profile="primary", trading_date="2026-01-05")
    assert alerts == []
```

```python
def test_fast_drop_near_support_with_volume_confirmation_alerts():
    alerts = build_movement_alerts("TSLL", _candles_with_break_and_volume(), profile="primary", trading_date="2026-01-05")
    assert alerts[0].event_type == "fast_drop"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/pytest -q tests/test_movement_alerts.py tests/test_movement_alert_integration.py`
Expected: FAIL on the new movement filter expectations

- [ ] **Step 3: Extend movement alert inputs with volume and level context**

```python
@dataclass(frozen=True)
class MovementAlert:
    ...
    volume_ratio: float
    level_context: str
```

```python
recent = candles_1m.sort_values("timestamp").tail(max(window_minutes, 2))
baseline = candles_1m.sort_values("timestamp").tail(max(window_minutes * 6, 20))
volume_ratio = _volume_ratio(recent, baseline)
level_context = _level_context(symbol_or_snapshot, latest_price)
```

- [ ] **Step 4: Gate movement alerts on confirmation, not raw move alone**

```python
if tier is None:
    return []
if volume_ratio < 1.8 and level_context == "none":
    return []
```

Use the existing `TechnicalSnapshot` when available so primary and benchmark symbols can confirm against support/resistance, while raw string-only symbols like `BTC-USD` can still alert on pure move plus volume expansion.

- [ ] **Step 5: Surface the extra context in alert payloads and dashboard history**

```python
payload = {
    ...
    "volume_ratio": alert.volume_ratio,
    "level_context": alert.level_context,
}
```

```python
"confidence": payload.get("repeat_count"),
"setup_type": f"{setup_type}:{payload.get('level_context')}" if payload.get("level_context") else setup_type,
```

- [ ] **Step 6: Run tests to verify the upgraded movement rules**

Run: `./.venv/bin/pytest -q tests/test_movement_alerts.py tests/test_movement_alert_integration.py`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add gemini_stock/rules/movement_alerts.py gemini_stock/main.py gemini_stock/storage/db.py gemini_stock/web/repository.py tests/test_movement_alerts.py tests/test_movement_alert_integration.py
git commit -m "feat: add confirmation filters to movement alerts"
```

### Task 4: Tighten Session Scheduling Around High-Value Windows

**Files:**
- Modify: `gemini_stock/schedule.py`
- Modify: `tests/test_schedule.py`
- Modify: `tests/test_health.py`
- Modify: `README.md`

- [ ] **Step 1: Write the failing tests**

```python
def test_regular_session_uses_five_minute_interval_midday():
    decision = get_schedule_decision(_utc(2026, 1, 5, 17, 0))
    assert decision.interval_seconds == 5 * 60
```

```python
def test_regular_session_uses_tighter_interval_near_close():
    decision = get_schedule_decision(_utc(2026, 1, 5, 20, 30))
    assert decision.interval_seconds == 2 * 60
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/pytest -q tests/test_schedule.py tests/test_health.py`
Expected: FAIL on the new interval expectations

- [ ] **Step 3: Replace the current two-band regular-session scheduler with four bands**

```python
def _regular_interval_seconds(current: datetime) -> int:
    regular_open = current.replace(hour=9, minute=30, second=0, microsecond=0)
    if regular_open <= current < regular_open + timedelta(minutes=60):
        return 60
    if regular_open + timedelta(minutes=60) <= current < regular_open + timedelta(hours=2, minutes=30):
        return 5 * 60
    if regular_open + timedelta(hours=2, minutes=30) <= current < current.replace(hour=14, minute=30, second=0, microsecond=0):
        return 10 * 60
    return 2 * 60
```

- [ ] **Step 4: Update docs and stale-health assumptions**

```md
盘中会按开盘、午间、尾盘不同阶段动态调整扫描频率；尾盘频率会重新加密。
```

Keep `evaluate_worker_health()` unchanged except for test expectations so stale thresholds still derive from the active interval.

- [ ] **Step 5: Run tests to verify schedule behavior**

Run: `./.venv/bin/pytest -q tests/test_schedule.py tests/test_health.py`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add gemini_stock/schedule.py tests/test_schedule.py tests/test_health.py README.md
git commit -m "feat: tune market session scheduling windows"
```

### Task 5: Add Dashboard Ranking Views For Triage

**Files:**
- Modify: `gemini_stock/web/repository.py`
- Modify: `gemini_stock/web/app.py`
- Modify: `tests/test_web_dashboard.py`
- Modify: `README.md`

- [ ] **Step 1: Write the failing tests**

```python
def test_dashboard_status_includes_priority_rankings(tmp_path, monkeypatch):
    client = TestClient(create_app(database_path=tmp_path / "signals.db", chart_dir=tmp_path))
    payload = client.get("/api/status").json()
    assert "priority_views" in payload
```

```python
def test_priority_views_include_most_urgent_and_most_actionable(tmp_path, monkeypatch):
    ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/pytest -q tests/test_web_dashboard.py`
Expected: FAIL because `priority_views` does not exist yet

- [ ] **Step 3: Add lightweight ranking helpers in the repository**

```python
def build_priority_views(self, symbols: list[str], benchmarks: list[str]) -> dict[str, list[dict[str, Any]]]:
    symbol_states = self.get_symbol_states(symbols)
    return {
        "most_urgent": _rank_most_urgent(symbol_states)[:3],
        "most_abnormal": _rank_most_abnormal(symbol_states)[:3],
        "most_actionable": _rank_most_actionable(symbol_states)[:3],
    }
```

Use existing fields only: freshness, latest price, analysis bias, confidence, setup type, alert state, and distance-to-zone approximations from entry/target bands.

- [ ] **Step 4: Expose the new rankings through API and HTML**

```python
return {
    "generated_at_utc": utc_now_iso(),
    ...
    "priority_views": repo.build_priority_views(symbols_now, benchmarks_now),
}
```

```python
priority_views = data.get("priority_views", {})
```

Render three compact sections near the top of the dashboard so the user sees what to watch first without scanning the full symbol list.

- [ ] **Step 5: Run tests to verify dashboard triage output**

Run: `./.venv/bin/pytest -q tests/test_web_dashboard.py`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add gemini_stock/web/repository.py gemini_stock/web/app.py tests/test_web_dashboard.py README.md
git commit -m "feat: add dashboard triage rankings"
```

### Task 6: End-to-End Regression Pass

**Files:**
- Modify: `README.md`
- Test: `tests/test_market_data_factory.py`
- Test: `tests/test_schemas_and_rules.py`
- Test: `tests/test_movement_alerts.py`
- Test: `tests/test_movement_alert_integration.py`
- Test: `tests/test_schedule.py`
- Test: `tests/test_health.py`
- Test: `tests/test_web_dashboard.py`

- [ ] **Step 1: Run the focused regression suite**

Run: `./.venv/bin/pytest -q tests/test_market_data_factory.py tests/test_schemas_and_rules.py tests/test_movement_alerts.py tests/test_movement_alert_integration.py tests/test_schedule.py tests/test_health.py tests/test_web_dashboard.py`
Expected: PASS

- [ ] **Step 2: Run the broader worker-facing regression suite**

Run: `./.venv/bin/pytest -q tests/test_tracing.py tests/test_storage.py tests/test_price_action_integration.py`
Expected: PASS

- [ ] **Step 3: Re-read monitoring docs for drift**

Check that `README.md` matches:
- `DATA_PROVIDER=auto` as the default recommendation
- event-scoped cooldown behavior
- movement-alert confirmation semantics
- segmented intraday scheduling
- dashboard priority views

- [ ] **Step 4: Commit the doc and regression cleanup**

```bash
git add README.md
git commit -m "docs: align monitoring docs with runtime behavior"
```
