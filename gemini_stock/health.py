from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from gemini_stock.schedule import ScheduleDecision


def evaluate_worker_health(
    latest_activity_at: str | None,
    decision: ScheduleDecision,
    now: datetime | None = None,
    stale_after_intervals: float = 2.5,
) -> dict[str, Any]:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if not decision.should_run:
        return {
            "state": "waiting",
            "is_stale": False,
            "age_seconds": None,
            "stale_after_seconds": int(decision.interval_seconds * stale_after_intervals),
        }
    stale_after_seconds = int(decision.interval_seconds * stale_after_intervals)
    if not latest_activity_at:
        return {
            "state": "no_data",
            "is_stale": True,
            "age_seconds": None,
            "stale_after_seconds": stale_after_seconds,
        }
    latest = _parse_datetime(latest_activity_at)
    age_seconds = max(0, int((current - latest).total_seconds()))
    is_stale = age_seconds > stale_after_seconds
    return {
        "state": "stale" if is_stale else "ok",
        "is_stale": is_stale,
        "age_seconds": age_seconds,
        "stale_after_seconds": stale_after_seconds,
    }


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
