"""Indicators: EMA, ATR, session-anchored VWAP, and fractal swing pivots.

Plain Python on Bar objects, no TA dependency. EMA, ATR and VWAP are
incremental; pivots need ``k`` bars of lookahead so they run over a window.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from .bars import Bar


class EMA:
    """Incremental exponential moving average."""

    def __init__(self, period: int):
        if period < 1:
            raise ValueError("EMA period must be >= 1")
        self.period = period
        self.alpha = 2.0 / (period + 1.0)
        self.value: Optional[float] = None
        self._count = 0

    def update(self, price: float) -> Optional[float]:
        self._count += 1
        if self.value is None:
            self.value = price
        else:
            self.value = self.alpha * price + (1 - self.alpha) * self.value
        return self.value

    @property
    def is_warm(self) -> bool:
        return self._count >= self.period


class ATR:
    """Wilder's Average True Range over ``period`` bars (incremental).

    The first ``period`` true ranges are simple-averaged to seed the value, then
    Wilder smoothing takes over.
    """

    def __init__(self, period: int):
        if period < 1:
            raise ValueError("ATR period must be >= 1")
        self.period = period
        self.value: Optional[float] = None
        self._prev_close: Optional[float] = None
        self._seed_sum = 0.0
        self._count = 0

    def update(self, bar: Bar) -> Optional[float]:
        if self._prev_close is None:
            tr = bar.high - bar.low
        else:
            tr = max(bar.high - bar.low,
                     abs(bar.high - self._prev_close),
                     abs(bar.low - self._prev_close))
        self._prev_close = bar.close
        self._count += 1
        if self.value is None:
            self._seed_sum += tr
            if self._count >= self.period:
                self.value = self._seed_sum / self.period
        else:
            self.value = (self.value * (self.period - 1) + tr) / self.period
        return self.value

    @property
    def is_warm(self) -> bool:
        return self.value is not None


class SessionVWAP:
    """Volume-weighted average price since the last :meth:`reset`.

    Uses typical price ``(H+L+C)/3``. A zero-volume bar (some feeds report 0 on
    quotes) contributes with weight 1 so VWAP keeps tracking price.
    """

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._pv = 0.0
        self._vol = 0.0
        self.value: Optional[float] = None

    def update(self, bar: Bar) -> float:
        typical = (bar.high + bar.low + bar.close) / 3.0
        weight = bar.volume if bar.volume > 0 else 1.0
        self._pv += typical * weight
        self._vol += weight
        self.value = self._pv / self._vol if self._vol else typical
        return self.value


@dataclass
class Pivot:
    index: int
    price: float
    kind: str         # "high" | "low"
    bar: Bar


def find_pivots(bars: Sequence[Bar], k: int) -> list[Pivot]:
    """Return confirmed fractal pivots.

    Bar ``i`` is a pivot high if its high strictly exceeds the highs of the ``k``
    bars on each side (mirror for lows). The last ``k`` bars lack right-side
    confirmation so they can't be pivots yet.
    """
    pivots: list[Pivot] = []
    n = len(bars)
    for i in range(k, n - k):
        hi = bars[i].high
        lo = bars[i].low
        left = bars[i - k:i]
        right = bars[i + 1:i + 1 + k]
        if all(hi > b.high for b in left) and all(hi > b.high for b in right):
            pivots.append(Pivot(i, hi, "high", bars[i]))
        if all(lo < b.low for b in left) and all(lo < b.low for b in right):
            pivots.append(Pivot(i, lo, "low", bars[i]))
    return pivots


def last_pivot(bars: Sequence[Bar], k: int, kind: str) -> Optional[Pivot]:
    """Most recent confirmed pivot of ``kind`` ("high"/"low"), or None."""
    for p in reversed(find_pivots(bars, k)):
        if p.kind == kind:
            return p
    return None
