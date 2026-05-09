from datetime import datetime, timezone

from gemini_stock.schedule import MarketSession, get_schedule_decision


def _utc(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def test_new_year_holiday_is_closed_even_during_regular_hours():
    # 2026-01-01 15:00 UTC = 10:00 New York on New Year's Day.
    decision = get_schedule_decision(_utc(2026, 1, 1, 15, 0))

    assert decision.should_run is False
    assert decision.session == MarketSession.CLOSED


def test_black_friday_early_close_switches_to_afterhours_after_1pm_new_york():
    # 2026-11-27 18:30 UTC = 13:30 New York on the day after Thanksgiving.
    decision = get_schedule_decision(_utc(2026, 11, 27, 18, 30))

    assert decision.should_run is True
    assert decision.session == MarketSession.AFTERHOURS
    assert decision.interval_seconds == 60 * 60
