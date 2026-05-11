from datetime import datetime, timezone

from gemini_stock.schedule import MarketSession, get_schedule_decision


def _utc(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def test_regular_market_opening_two_hours_run_every_five_minutes():
    # 2026-01-05 15:00 UTC = 10:00 New York (opening window)
    decision = get_schedule_decision(_utc(2026, 1, 5, 15, 0))

    assert decision.should_run is True
    assert decision.session == MarketSession.REGULAR
    assert decision.interval_seconds == 5 * 60


def test_regular_market_after_opening_window_runs_every_thirty_minutes():
    # 2026-01-05 17:00 UTC = 12:00 New York
    decision = get_schedule_decision(_utc(2026, 1, 5, 17, 0))

    assert decision.should_run is True
    assert decision.session == MarketSession.REGULAR
    assert decision.interval_seconds == 30 * 60


def test_premarket_runs_every_thirty_minutes():
    # 2026-01-05 13:00 UTC = 08:00 New York
    decision = get_schedule_decision(_utc(2026, 1, 5, 13, 0))

    assert decision.should_run is True
    assert decision.session == MarketSession.PREMARKET
    assert decision.interval_seconds == 30 * 60


def test_afterhours_runs_every_hour():
    # 2026-01-05 22:00 UTC = 17:00 New York
    decision = get_schedule_decision(_utc(2026, 1, 5, 22, 0))

    assert decision.should_run is True
    assert decision.session == MarketSession.AFTERHOURS
    assert decision.interval_seconds == 60 * 60


def test_overnight_runs_every_hour_from_sunday_evening():
    # 2026-01-05 02:00 UTC = Sunday 21:00 New York
    decision = get_schedule_decision(_utc(2026, 1, 5, 2, 0))

    assert decision.should_run is False
    assert decision.session == MarketSession.OVERNIGHT
    assert decision.interval_seconds > 0


def test_overnight_runs_every_hour_before_premarket():
    # 2026-01-05 07:00 UTC = Monday 02:00 New York
    decision = get_schedule_decision(_utc(2026, 1, 5, 7, 0))

    assert decision.should_run is False
    assert decision.session == MarketSession.OVERNIGHT
    assert decision.interval_seconds > 0


def test_weekend_daytime_market_does_not_run():
    # 2026-01-03 17:00 UTC = Saturday 12:00 New York
    decision = get_schedule_decision(_utc(2026, 1, 3, 17, 0))

    assert decision.should_run is False
    assert decision.session == MarketSession.CLOSED
    assert decision.interval_seconds > 0
