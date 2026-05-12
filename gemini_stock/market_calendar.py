from __future__ import annotations

from datetime import date, datetime, time, timedelta

from gemini_stock.data.base import NEW_YORK


EARLY_CLOSE_TIME = time(13, 0)
REGULAR_CLOSE_TIME = time(16, 0)


def is_trading_day(value: datetime | date) -> bool:
    current = _as_date(value)
    if current.weekday() >= 5:
        return False
    return current not in market_holidays(current.year)


def regular_close_time(value: datetime | date) -> time:
    return EARLY_CLOSE_TIME if _as_date(value) in early_close_days(_as_date(value).year) else REGULAR_CLOSE_TIME


def next_trading_day(value: datetime | date) -> date:
    current = _as_date(value) + timedelta(days=1)
    while not is_trading_day(current):
        current += timedelta(days=1)
    return current


def previous_trading_day(value: datetime | date) -> date:
    current = _as_date(value) - timedelta(days=1)
    while not is_trading_day(current):
        current -= timedelta(days=1)
    return current


def latest_completed_trading_day(value: datetime) -> date:
    current = value.astimezone(NEW_YORK)
    if is_trading_day(current) and current.time() >= regular_close_time(current):
        return current.date()
    return previous_trading_day(current)


def market_holidays(year: int) -> set[date]:
    thanksgiving = _nth_weekday(year, 11, 3, 4)
    return {
        _observed(date(year, 1, 1)),
        _nth_weekday(year, 1, 0, 3),
        _nth_weekday(year, 2, 0, 3),
        _easter_date(year) - timedelta(days=2),
        _last_weekday(year, 5, 0),
        _observed(date(year, 6, 19)),
        _observed(date(year, 7, 4)),
        _nth_weekday(year, 9, 0, 1),
        thanksgiving,
        _observed(date(year, 12, 25)),
    }


def early_close_days(year: int) -> set[date]:
    thanksgiving = _nth_weekday(year, 11, 3, 4)
    days = {
        thanksgiving + timedelta(days=1),
        date(year, 12, 24),
    }
    july_fourth = date(year, 7, 4)
    if july_fourth.weekday() == 1:
        days.add(date(year, 7, 3))
    if july_fourth.weekday() == 3:
        days.add(date(year, 7, 5))
    return {day for day in days if is_trading_day(day)}


def _as_date(value: datetime | date) -> date:
    if isinstance(value, datetime):
        return value.astimezone(NEW_YORK).date()
    return value


def _observed(day: date) -> date:
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    current = date(year, month, 1)
    offset = (weekday - current.weekday()) % 7
    return current + timedelta(days=offset + (n - 1) * 7)


def _last_weekday(year: int, month: int, weekday: int) -> date:
    current = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)
    return current - timedelta(days=(current.weekday() - weekday) % 7)


def _easter_date(year: int) -> date:
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)
