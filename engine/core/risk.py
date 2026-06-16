"""Risk guards and the session clock.

SessionClock answers "may I open now?" and "must I flatten?" using the trading
window and flatten time in US/Eastern. RiskManager handles pre-entry gating
(daily loss, trade count, consecutive losses, trailing-drawdown headroom) and
the trailing-drawdown model for a live funded account. It holds no engine
references; the engine passes the current tallies in and acts on the verdict.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from infra.config import MarketConfig, RiskConfig

log = logging.getLogger("risk")
ET = ZoneInfo("America/New_York")


def _parse_hhmm(value: str) -> time:
    hh, mm = value.split(":")
    return time(int(hh), int(mm))


class SessionClock:
    """Trading-window and flatten-time logic in US/Eastern.

    Handles the RTH window and, when ``extended_hours`` is set, an overnight
    Globex window that wraps past midnight (``start`` > ``end``). Comparisons go
    through :meth:`_in_cycle`, which treats the day as a circular clock so an
    overnight window like 18:00->17:00 works like a same-day one.
    """

    def __init__(self, market: MarketConfig):
        self.extended = market.extended_hours
        if self.extended:
            win = market.extended_window
            self.flatten = _parse_hhmm(market.extended_flatten_et)
        else:
            win = market.trading_window
            self.flatten = _parse_hhmm(market.flatten_et)
        self.start = _parse_hhmm(win.start_et)
        self.end = _parse_hhmm(win.end_et)
        self._wrap = self.end <= self.start  # overnight

    @staticmethod
    def _et(now_utc: datetime) -> datetime:
        return now_utc.astimezone(ET)

    @staticmethod
    def _in_cycle(t: time, lo: time, hi: time) -> bool:
        """True if ``t`` is in [lo, hi) on a 24h clock; ``lo > hi`` wraps midnight."""
        if lo <= hi:
            return lo <= t < hi
        return t >= lo or t < hi

    def session_key(self, now_utc: datetime) -> str:
        """Session identifier used to detect rollover.

        RTH keys by ET calendar date. In extended mode the session keeps its
        identity across midnight until the next open, so overnight tallies and
        strategy state don't reset at 00:00 ET.
        """
        et_dt = self._et(now_utc)
        if self.extended:
            anchor = (et_dt if et_dt.time() >= self.start
                      else et_dt - timedelta(days=1))
            return anchor.strftime("%Y-%m-%d")
        return et_dt.strftime("%Y-%m-%d")

    def within_window(self, now_utc: datetime) -> bool:
        return self._in_cycle(self._et(now_utc).time(), self.start, self.end)

    def can_open(self, now_utc: datetime) -> bool:
        """No new entries after the flatten time even if the window is open."""
        return self._in_cycle(self._et(now_utc).time(), self.start, self.flatten)

    def should_flatten(self, now_utc: datetime) -> bool:
        """True in the flatten band [flatten, end), the run-up to session close."""
        return self._in_cycle(self._et(now_utc).time(), self.flatten, self.end)


@dataclass
class RiskVerdict:
    allowed: bool
    reason: str = ""
    halt: bool = False   # engine should transition to HALTED


class TrailingDrawdown:
    """Live-funded trailing max-loss model.

    The loss floor trails peak equity by ``dd`` but never exceeds the starting
    balance: once enough profit is banked the floor locks at the starting
    balance (TopStep funded behaviour).
    """

    def __init__(self, starting_balance: float, dd: float):
        self.start = starting_balance
        self.dd = dd
        self.peak_equity = starting_balance

    def update(self, equity: float) -> None:
        if equity > self.peak_equity:
            self.peak_equity = equity

    @property
    def floor(self) -> float:
        return min(self.peak_equity - self.dd, self.start)

    def headroom(self, equity: float) -> float:
        return equity - self.floor


class RiskManager:
    def __init__(self, cfg: RiskConfig, starting_balance: float):
        self.cfg = cfg
        self.starting_balance = starting_balance
        self.dd = TrailingDrawdown(starting_balance, cfg.trailing_drawdown_currency)

    # equity / drawdown
    def update_equity(self, equity: float) -> None:
        self.dd.update(equity)

    def headroom(self, equity: float) -> Optional[float]:
        if not self.cfg.trailing_drawdown_guard_enabled:
            return None
        return self.dd.headroom(equity)

    # pre-entry gate
    def can_enter(self, *, equity: float, realized_pnl_today: float,
                  trades_today: int, consecutive_losses: int,
                  projected_risk: float) -> RiskVerdict:
        # hard stops first: these halt the engine, not just block one entry
        if realized_pnl_today <= -self.cfg.daily_loss_limit_currency:
            return RiskVerdict(False, "daily loss limit reached", halt=True)
        if (self.cfg.daily_profit_limit_currency > 0
                and realized_pnl_today >= self.cfg.daily_profit_limit_currency):
            return RiskVerdict(False, "daily profit target reached", halt=True)
        if consecutive_losses >= self.cfg.max_consecutive_losses:
            return RiskVerdict(False, "max consecutive losses reached", halt=True)

        if trades_today >= self.cfg.max_trades_per_day:
            return RiskVerdict(False, "max trades/day reached")

        if self.cfg.trailing_drawdown_guard_enabled:
            headroom = self.dd.headroom(equity)
            needed = self.cfg.trailing_drawdown_buffer + projected_risk
            if headroom < needed:
                return RiskVerdict(
                    False,
                    f"trailing-drawdown guard: headroom ${headroom:.0f} < "
                    f"${needed:.0f} (buffer+risk)",
                )

        return RiskVerdict(True)

    # continuous breach check (called each loop)
    def breach(self, *, realized_pnl_today: float,
               consecutive_losses: int) -> Optional[str]:
        if realized_pnl_today <= -self.cfg.daily_loss_limit_currency:
            return "daily loss limit reached"
        if (self.cfg.daily_profit_limit_currency > 0
                and realized_pnl_today >= self.cfg.daily_profit_limit_currency):
            return "daily profit target reached"
        if consecutive_losses >= self.cfg.max_consecutive_losses:
            return "max consecutive losses reached"
        return None
