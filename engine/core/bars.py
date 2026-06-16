"""Aggregates ticks into fixed-interval OHLCV bars.

Ticks fold into N-minute bars aligned to wall-clock boundaries; a closed bar is
emitted when a tick crosses into the next interval, or via ``flush`` when the
interval elapses with no fresh tick (thin markets). Timestamps are UTC.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional


@dataclass
class Bar:
    start: datetime           # interval start (UTC)
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    @property
    def end(self) -> datetime:
        # Set by the aggregator when emitted; falls back to start.
        return getattr(self, "_end", self.start)

    def is_bullish(self) -> bool:
        return self.close > self.open

    def is_bearish(self) -> bool:
        return self.close < self.open


def floor_to_interval(ts: datetime, minutes: int) -> datetime:
    """Floor a UTC timestamp to the start of its N-minute interval."""
    ts = ts.astimezone(timezone.utc)
    discard = timedelta(
        minutes=ts.minute % minutes,
        seconds=ts.second,
        microseconds=ts.microsecond,
    )
    return ts - discard


class BarAggregator:
    """Aggregates ticks into closed bars and invokes ``on_bar`` for each."""

    def __init__(self, timeframe_minutes: int,
                 on_bar: Optional[Callable[[Bar], None]] = None):
        self.timeframe = timeframe_minutes
        self.on_bar = on_bar
        self._current: Optional[Bar] = None

    def add_tick(self, ts: datetime, price: float, volume: float = 0.0) -> Optional[Bar]:
        """Fold one tick in. Returns a closed bar if this tick ended one."""
        ts = ts.astimezone(timezone.utc)
        bucket = floor_to_interval(ts, self.timeframe)
        closed: Optional[Bar] = None

        if self._current is None:
            self._current = Bar(bucket, price, price, price, price, volume)
            return None

        if bucket > self._current.start:
            # tick is in a later interval, so close the running bar
            closed = self._close(bucket)
            self._current = Bar(bucket, price, price, price, price, volume)
        else:
            b = self._current
            b.high = max(b.high, price)
            b.low = min(b.low, price)
            b.close = price
            b.volume += volume
        return closed

    def flush(self, now: datetime) -> Optional[Bar]:
        """Close the running bar if its interval has fully elapsed by ``now``."""
        if self._current is None:
            return None
        now = now.astimezone(timezone.utc)
        bucket = floor_to_interval(now, self.timeframe)
        if bucket > self._current.start:
            closed = self._close(bucket)
            self._current = None
            return closed
        return None

    def _close(self, next_bucket: datetime) -> Bar:
        b = self._current
        assert b is not None
        b._end = b.start + timedelta(minutes=self.timeframe)  # type: ignore[attr-defined]
        if self.on_bar is not None:
            self.on_bar(b)
        return b

    @property
    def current(self) -> Optional[Bar]:
        return self._current
