from datetime import datetime, timezone

from gemini_stock.schedule import MarketSession, ai_analysis_window_open, get_schedule_decision


def _utc(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def test_regular_market_first_ninety_minutes_run_every_minute():
    # 2026-01-05 15:00 UTC = 10:00 New York (opening window)
    decision = get_schedule_decision(_utc(2026, 1, 5, 15, 0))

    assert decision.should_run is True
    assert decision.session == MarketSession.REGULAR
    assert decision.interval_seconds == 60


def test_regular_market_at_ninety_minute_boundary_runs_every_five_minutes_before_beijing_quiet_hours():
    # 2026-07-06 15:00 UTC = 11:00 New York = 23:00 Beijing
    decision = get_schedule_decision(_utc(2026, 7, 6, 15, 0))

    assert decision.should_run is True
    assert decision.session == MarketSession.REGULAR
    assert decision.interval_seconds == 5 * 60


def test_regular_market_midday_drops_to_thirty_minutes_during_beijing_quiet_hours():
    # 2026-01-05 17:00 UTC = 12:00 New York
    decision = get_schedule_decision(_utc(2026, 1, 5, 17, 0))

    assert decision.should_run is True
    assert decision.session == MarketSession.REGULAR
    assert decision.interval_seconds == 30 * 60


def test_regular_market_near_close_drops_to_thirty_minutes_during_beijing_quiet_hours():
    # 2026-01-05 20:00 UTC = 15:00 New York
    decision = get_schedule_decision(_utc(2026, 1, 5, 20, 0))

    assert decision.should_run is True
    assert decision.session == MarketSession.REGULAR
    assert decision.interval_seconds == 30 * 60


def test_premarket_runs_every_five_minutes():
    # 2026-01-05 13:00 UTC = 08:00 New York
    decision = get_schedule_decision(_utc(2026, 1, 5, 13, 0))

    assert decision.should_run is True
    assert decision.session == MarketSession.PREMARKET
    assert decision.interval_seconds == 5 * 60


def test_afterhours_runs_every_hour():
    # 2026-01-05 22:00 UTC = 17:00 New York
    decision = get_schedule_decision(_utc(2026, 1, 5, 22, 0))

    assert decision.should_run is True
    assert decision.session == MarketSession.AFTERHOURS
    assert decision.interval_seconds == 60 * 60


def test_overnight_runs_every_five_minutes_from_sunday_evening():
    # 2026-01-05 02:00 UTC = Sunday 21:00 New York
    decision = get_schedule_decision(_utc(2026, 1, 5, 2, 0))

    assert decision.should_run is True
    assert decision.session == MarketSession.OVERNIGHT
    assert decision.interval_seconds == 5 * 60


def test_overnight_runs_every_five_minutes_before_premarket():
    # 2026-01-05 07:00 UTC = Monday 02:00 New York
    decision = get_schedule_decision(_utc(2026, 1, 5, 7, 0))

    assert decision.should_run is True
    assert decision.session == MarketSession.OVERNIGHT
    assert decision.interval_seconds == 5 * 60


def test_regular_market_drops_to_thirty_minutes_during_beijing_quiet_hours():
    # 2026-01-05 15:45 UTC = 10:45 New York = 23:45 Beijing
    decision = get_schedule_decision(_utc(2026, 1, 5, 15, 45))

    assert decision.should_run is True
    assert decision.session == MarketSession.REGULAR
    assert decision.interval_seconds == 30 * 60


def test_regular_market_keeps_fast_frequency_before_beijing_quiet_hours():
    # 2026-01-05 15:25 UTC = 10:25 New York = 23:25 Beijing
    decision = get_schedule_decision(_utc(2026, 1, 5, 15, 25))

    assert decision.should_run is True
    assert decision.session == MarketSession.REGULAR
    assert decision.interval_seconds == 60


def test_afterhours_keeps_hourly_frequency_during_beijing_quiet_hours():
    # 2026-01-06 00:30 UTC = 19:30 New York = 08:30 Beijing
    decision = get_schedule_decision(_utc(2026, 1, 6, 0, 30))

    assert decision.should_run is True
    assert decision.session == MarketSession.AFTERHOURS
    assert decision.interval_seconds == 60 * 60


def test_weekend_daytime_market_does_not_run():
    # 2026-01-03 17:00 UTC = Saturday 12:00 New York
    decision = get_schedule_decision(_utc(2026, 1, 3, 17, 0))

    assert decision.should_run is False
    assert decision.session == MarketSession.CLOSED
    assert decision.interval_seconds > 0


def test_ai_analysis_window_open_during_regular_opening_hours_in_winter():
    # 2026-01-05 15:30 UTC = 10:30 New York
    assert ai_analysis_window_open(_utc(2026, 1, 5, 15, 30)) is True


def test_ai_analysis_window_closed_after_opening_hours_in_winter():
    # 2026-01-05 17:00 UTC = 12:00 New York
    assert ai_analysis_window_open(_utc(2026, 1, 5, 17, 0)) is False


def test_ai_analysis_window_open_during_regular_opening_hours_in_summer():
    # 2026-07-06 14:30 UTC = 10:30 New York
    assert ai_analysis_window_open(_utc(2026, 7, 6, 14, 30)) is True


def test_ai_analysis_window_closed_in_premarket_even_if_beijing_is_evening():
    # 2026-07-06 12:30 UTC = 08:30 New York
    assert ai_analysis_window_open(_utc(2026, 7, 6, 12, 30)) is False
