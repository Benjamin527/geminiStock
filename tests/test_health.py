from datetime import datetime, timedelta, timezone

from gemini_stock.health import evaluate_worker_health
from gemini_stock.schedule import MarketSession, ScheduleDecision


def test_worker_health_is_waiting_when_market_is_not_running():
    status = evaluate_worker_health(
        latest_llm_at=None,
        decision=ScheduleDecision(False, MarketSession.OVERNIGHT, 6 * 60 * 60),
        now=datetime(2026, 1, 5, 2, 0, tzinfo=timezone.utc),
    )

    assert status["state"] == "waiting"
    assert status["is_stale"] is False


def test_worker_health_marks_stale_when_scan_is_too_old_during_run_window():
    now = datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc)
    status = evaluate_worker_health(
        latest_llm_at=(now - timedelta(minutes=90)).isoformat(),
        decision=ScheduleDecision(True, MarketSession.REGULAR, 20 * 60),
        now=now,
    )

    assert status["state"] == "stale"
    assert status["is_stale"] is True
    assert status["age_seconds"] == 90 * 60


def test_worker_health_is_ok_when_scan_is_recent():
    now = datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc)
    status = evaluate_worker_health(
        latest_llm_at=(now - timedelta(minutes=18)).isoformat(),
        decision=ScheduleDecision(True, MarketSession.REGULAR, 20 * 60),
        now=now,
    )

    assert status["state"] == "ok"
    assert status["is_stale"] is False
