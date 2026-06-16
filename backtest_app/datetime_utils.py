"""New York (ET) datetime helpers for the backtest UI."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")


def utc_to_et(dt: datetime) -> datetime:
    return dt.astimezone(_ET)


def et_to_utc(d: date, t: time) -> datetime:
    """Combine ET date + time and return UTC-aware datetime."""
    local = datetime.combine(d, t, tzinfo=_ET)
    return local.astimezone(timezone.utc)


def fmt_et(dt: datetime | None, *, date_only: bool = False) -> str:
    if dt is None:
        return "—"
    et = utc_to_et(dt)
    if date_only:
        return et.strftime("%Y-%m-%d")
    return et.strftime("%Y-%m-%d %H:%M ET")


def fmt_et_time(dt: datetime | None) -> str:
    if dt is None:
        return "—"
    return utc_to_et(dt).strftime("%H:%M ET")


def bar_close_et(bar_start: datetime, timeframe_minutes: int) -> datetime:
    """Return the bar close timestamp (UTC-aware)."""
    return bar_start + timedelta(minutes=timeframe_minutes)


def fmt_bar_end_et(bar_start: datetime | None, timeframe_minutes: int) -> str:
    """Format inclusive end of a bar window in ET."""
    if bar_start is None:
        return "—"
    return fmt_et(bar_close_et(bar_start, timeframe_minutes))
