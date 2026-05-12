# Guance ddtrace Tracing Design

## Goal

Report tracing data from both runtime containers to the Guance trace agent at `121.196.154.93:9529` using the `ddtrace-python` integration approach recommended by Guance.

The feature should cover:

- the long-running worker container
- the FastAPI dashboard container

The design should stay simple for operations: tracing is enabled through Docker startup commands and environment variables, with only a small amount of manual span creation added to the worker so one scan cycle appears as a coherent trace.

## External Guidance

This design follows the documented Guance `ddtrace-python` integration pattern:

- install `ddtrace`
- start Python with `ddtrace-run`
- configure the Datadog-compatible trace agent host and port through environment variables

It also relies on `ddtrace` framework auto-instrumentation for FastAPI when launched through `ddtrace-run`.

## Scope

- Add `ddtrace` as a runtime dependency.
- Enable tracing for both Docker services.
- Send traces to:
  - `DD_AGENT_HOST=121.196.154.93`
  - `DD_AGENT_PORT=9529`
  - `DD_TRACE_AGENT_PORT=9529`
- Default runtime metadata:
  - `DD_ENV=local`
  - `DD_LOGS_INJECTION=true`
- Default service names:
  - worker: `gemini-worker`
  - dashboard: `gemini-dashboard`
- Add a small amount of worker-only manual tracing so a full worker cycle is represented as one trace with child spans.

Out of scope for this change:

- distributed tracing across external upstream systems
- custom dashboards in Guance
- metric or log pipeline redesign
- deep manual spans for every helper in the codebase

## Proposed Approach

### Container Startup

Use `ddtrace-run` for both Docker entrypoints:

- worker:
  - `ddtrace-run python -m gemini_stock.main`
- dashboard:
  - `ddtrace-run python -m gemini_stock.web.app`

This keeps the integration aligned with Guance guidance and avoids invasive framework-specific bootstrap code.

### Environment Variables

Add trace-related defaults to `.env.example` and pass them through existing `env_file` usage in Docker Compose:

```bash
DD_AGENT_HOST=121.196.154.93
DD_AGENT_PORT=9529
DD_TRACE_AGENT_PORT=9529
DD_ENV=local
DD_LOGS_INJECTION=true
DD_SERVICE=gemini-worker
DD_DASHBOARD_SERVICE=gemini-dashboard
```

`DD_SERVICE` should be used by the worker container directly. The dashboard container should override service naming at the compose service level so it reports as `gemini-dashboard`.

This means:

- worker gets `DD_SERVICE=gemini-worker`
- dashboard gets `DD_SERVICE=gemini-dashboard` through a compose override

The separate `DD_DASHBOARD_SERVICE` setting exists only as a local config convenience if we want to keep names visible in `.env.example`; the actual runtime variable consumed by `ddtrace` remains `DD_SERVICE`.

### Worker Manual Spans

The worker is not an HTTP request/response service, so `ddtrace-run` alone would give incomplete traces. To make traces operationally useful, add a small manual tracing layer:

- root span around each `run_once()` cycle
- child span around each `run_symbol()` execution
- child span around `run_movement_only_once()`
- child span around maintenance tasks if they run in the cycle

Recommended span names:

- `worker.run_once`
- `worker.run_symbol`
- `worker.run_movement_only`
- `worker.maintenance`

Recommended tags:

- `symbol`
- `profile`
- `session`
- `llm_provider`
- `data_provider`
- `analysis_level` where applicable
- `error` or exception details through tracer error reporting

This is intentionally narrow. The goal is one readable trace per cycle, not full instrumentation of every branch.

### Dashboard Tracing

The dashboard should rely on `ddtrace-run` auto-instrumentation for:

- FastAPI request spans
- downstream supported library spans where available

No dashboard-specific manual spans are needed in version one unless verification shows missing request traces.

## File-Level Changes

- `pyproject.toml`
  - add `ddtrace` runtime dependency
- `.env.example`
  - add trace agent configuration defaults
- `Dockerfile`
  - no structural change expected beyond ensuring `ddtrace` is installed through project dependencies
- `docker-compose.yml`
  - change both service commands to `ddtrace-run ...`
  - set worker and dashboard `DD_SERVICE` appropriately
- `gemini_stock/main.py`
  - add manual worker spans
- `README.md`
  - document tracing configuration and Docker behavior
- tests:
  - add focused tests for worker tracing wrapper behavior and config wiring where practical

## Error Handling

- If trace delivery fails, the app should continue running.
- Tracing must remain non-blocking for market scanning and dashboard serving.
- Manual tracing code should avoid changing current business outcomes or exception flow.

## Testing Strategy

Add tests for:

- worker tracing wrapper creates a root cycle span and nested symbol spans
- worker tracing does not change behavior when symbol runs fail
- compose/runtime configuration selects `ddtrace-run` for both containers
- service names differ between worker and dashboard runtime configuration

Also run the existing full test suite to ensure tracing changes do not break normal behavior.

## Risks and Tradeoffs

- `ddtrace-run` is the lowest-risk way to integrate, but the worker still needs small manual spans because it is not request-driven.
- Using `DD_SERVICE` per container is operationally simple, but it requires an explicit dashboard override in Compose so both services do not collapse under one name.
- Introducing `ddtrace` adds startup overhead and one more external delivery path, but it is the most direct fit for the Guance integration you referenced.

## Final Recommendation

Implement tracing exactly at the container boundary with `ddtrace-run`, keep service naming explicit per container, and add only a small manual span structure in the worker. This gives useful traces quickly without turning the runtime into a tracing-heavy refactor.
