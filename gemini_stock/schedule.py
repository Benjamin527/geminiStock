from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from enum import StrEnum

from gemini_stock.data.base import NEW_YORK
from gemini_stock.market_calendar import is_trading_day, next_trading_day, regular_close_time


class MarketSession(StrEnum):
    OVERNIGHT = "overnight"
    PREMARKET = "premarket"
    REGULAR = "regular"
    AFTERHOURS = "afterhours"
    CLOSED = "closed"


@dataclass(frozen=True)
class ScheduleDecision:
    should_run: bool
    session: MarketSession
    interval_seconds: int


def classify_market_session(now: datetime | None = None) -> MarketSession:
    current = (now or datetime.now(NEW_YORK)).astimezone(NEW_YORK)
    current_time = current.time()
    if _is_overnight_session(current):
        return MarketSession.OVERNIGHT
    if not is_trading_day(current):
        return MarketSession.CLOSED
    if time(4, 0) <= current_time < time(9, 30):
        return MarketSession.PREMARKET
    if time(9, 30) <= current_time < regular_close_time(current):
        return MarketSession.REGULAR
    if regular_close_time(current) <= current_time < time(20, 0):
        return MarketSession.AFTERHOURS
    return MarketSession.CLOSED


def get_schedule_decision(now: datetime | None = None) -> ScheduleDecision:
    current = (now or datetime.now(NEW_YORK)).astimezone(NEW_YORK)
    session = classify_market_session(now)
    if session == MarketSession.OVERNIGHT:
        return ScheduleDecision(False, session, seconds_until_next_session(now))
    if session == MarketSession.PREMARKET:
        return ScheduleDecision(True, session, 30 * 60)
    if session == MarketSession.REGULAR:
        return ScheduleDecision(True, session, _regular_interval_seconds(current))
    if session == MarketSession.AFTERHOURS:
        return ScheduleDecision(True, session, 60 * 60)
    return ScheduleDecision(False, session, seconds_until_next_session(now))


def seconds_until_next_session(now: datetime | None = None) -> int:
    current = (now or datetime.now(NEW_YORK)).astimezone(NEW_YORK)
    next_starts = [_next_premarket_start(current), _next_overnight_start(current)]
    seconds = int((min(next_starts) - current).total_seconds())
    return max(seconds, 60)


def _is_overnight_session(current: datetime) -> bool:
    current_time = current.time()
    weekday = current.weekday()
    if time(20, 0) <= current_time and weekday in {6, 0, 1, 2, 3}:
        return True
    if current_time < time(4, 0) and weekday in {0, 1, 2, 3, 4}:
        return True
    return False


def _next_premarket_start(current: datetime) -> datetime:
    candidate = current.replace(hour=4, minute=0, second=0, microsecond=0)
    if candidate <= current:
        candidate = candidate.replace(
            year=next_trading_day(current).year,
            month=next_trading_day(current).month,
            day=next_trading_day(current).day,
        )
    while not is_trading_day(candidate):
        next_day = next_trading_day(candidate)
        candidate = candidate.replace(year=next_day.year, month=next_day.month, day=next_day.day)
    return candidate


def _next_overnight_start(current: datetime) -> datetime:
    candidate = current.replace(hour=20, minute=0, second=0, microsecond=0)
    if candidate <= current:
        candidate = candidate + timedelta(days=1)
    while candidate.weekday() not in {6, 0, 1, 2, 3}:
        candidate = candidate + timedelta(days=1)
    return candidate


def _regular_interval_seconds(current: datetime) -> int:
    regular_open = current.replace(hour=9, minute=30, second=0, microsecond=0)
    first_two_hours_end = regular_open + timedelta(hours=2)
    if regular_open <= current < first_two_hours_end:
        return 5 * 60
    return 30 * 60
