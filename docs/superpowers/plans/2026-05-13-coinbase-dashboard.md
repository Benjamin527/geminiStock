# Coinbase Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reskin and restructure the FastAPI dashboard into a Coinbase-style frontend using a template plus static assets while preserving existing API behavior.

**Architecture:** Keep repository queries and fragment generation server-side, but move the page shell, styles, and browser behavior into dedicated frontend files. Update page tests first so the redesign is driven by observable output rather than manual inspection.

**Tech Stack:** Python, FastAPI, StaticFiles, server-rendered HTML, standalone CSS/JS, pytest, Docker Compose

---

### Task 1: Lock the new UI contract in tests

**Files:**
- Modify: `tests/test_web_dashboard.py`
- Test: `tests/test_web_dashboard.py`

- [ ] Add assertions for the new shell behavior: template-based page still returns 200, includes Coinbase-style title copy, references `/static/dashboard.css` and `/static/dashboard.js`, and keeps the dashboard countdown hook.
- [ ] Run `pytest tests/test_web_dashboard.py -q` and confirm the new assertions fail for the current inline page.

### Task 2: Split the page shell into template and static assets

**Files:**
- Create: `gemini_stock/web/templates/dashboard.html`
- Create: `gemini_stock/web/static/dashboard.css`
- Create: `gemini_stock/web/static/dashboard.js`
- Modify: `gemini_stock/web/app.py`
- Test: `tests/test_web_dashboard.py`

- [ ] Mount a `/static` directory in the FastAPI app and load the template file from disk.
- [ ] Move inline CSS into `gemini_stock/web/static/dashboard.css`, rewriting the visual system to match `DESIGN.md` with white canvas, Coinbase blue accents, restrained cards, and light information density.
- [ ] Move inline browser behavior into `gemini_stock/web/static/dashboard.js`, preserving refresh, countdown, watchlist actions, threshold save, and mobile tab switching.
- [ ] Replace the inline HTML shell in `render_dashboard()` with template rendering that injects existing fragments and server values into the new file-based template.
- [ ] Run `pytest tests/test_web_dashboard.py -q` and make the updated page tests pass.

### Task 3: Refine fragment markup for the new visual hierarchy

**Files:**
- Modify: `gemini_stock/web/app.py`
- Test: `tests/test_web_dashboard.py`

- [ ] Adjust fragment and card markup so the summary strip, priority board, symbol cards, benchmark cards, and activity sections fit the Coinbase layout without changing API payloads.
- [ ] Keep existing user actions and labels, but simplify headings and supporting copy where needed for the new institutional style.
- [ ] Run `pytest tests/test_web_dashboard.py -q` and confirm no regressions in dashboard rendering tests.

### Task 4: Verify end-to-end in containers

**Files:**
- Modify: `gemini_stock/web/app.py`
- Modify: `gemini_stock/web/templates/dashboard.html`
- Modify: `gemini_stock/web/static/dashboard.css`
- Modify: `gemini_stock/web/static/dashboard.js`

- [ ] Run targeted tests for the dashboard.
- [ ] Build with `docker compose build`.
- [ ] Start services with `docker compose up -d`.
- [ ] Check service state with `docker compose ps` and verify the dashboard health endpoint responds successfully.
