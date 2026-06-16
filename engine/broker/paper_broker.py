"""Paper broker: simulated fills against the live bar stream.

Fill model:
  * Entry fills at the next bar's open with slippage; ``signal_close`` fills at
    the triggering bar's close instead.
  * Stop and target are tracked against each later bar's range.
  * If one bar straddles both stop and target, the stop is assumed hit first.
  * Slippage is adverse; commission is charged per side.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Optional

from core.bars import Bar
from core.state import Position, Side, Signal, Trade, utcnow
from infra.config import ContractSpec, ExecutionConfig, ExitConfig

from .base import Broker

log = logging.getLogger("paper_broker")

# Break-even stop is set a hair into profit so a flat stop-out still covers fees.
_BE_BUFFER_TICKS = 2


class PaperBroker(Broker):
    mode = "paper"

    def __init__(self, execution: ExecutionConfig, spec: ContractSpec,
                 exits: Optional[ExitConfig] = None):
        super().__init__()
        self.exec = execution
        self.spec = spec
        self.exits = exits or ExitConfig()
        self._position: Optional[Position] = None
        self._pending: Optional[tuple[Signal, int]] = None

    @property
    def position(self) -> Optional[Position]:
        return self._position

    @property
    def has_pending_entry(self) -> bool:
        return self._pending is not None

    def submit_entry(self, signal: Signal, size: int, bar: Bar) -> str:
        if self._position is not None:
            raise RuntimeError("submit_entry while a position is open")
        trade_id = uuid.uuid4().hex[:12]
        if self.exec.entry_fill_model == "signal_close":
            self._fill_entry(signal, size, trade_id, bar.close, bar.end or utcnow())
        else:  # next_open
            self._pending = (signal, size)
            log.debug("pending %s entry queued (id=%s) for next-bar open",
                      signal.side.value, trade_id)
            self._pending_id = trade_id
        return trade_id

    def cancel_pending(self) -> None:
        if self._pending is not None:
            log.info("cancelled pending entry")
        self._pending = None

    def on_bar(self, bar: Bar) -> None:
        # Fill a queued next-open entry at this bar's open.
        if self._pending is not None and self._position is None:
            signal, size = self._pending
            self._pending = None
            self._fill_entry(signal, size, getattr(self, "_pending_id",
                             uuid.uuid4().hex[:12]), bar.open,
                             bar.start)
        if self._position is not None:
            self._check_exit(bar)

    def flatten(self, reason: str, price: float, when: datetime) -> None:
        self.cancel_pending()
        if self._position is not None:
            self._close(price, when, reason)

    def _fill_entry(self, signal: Signal, size: int, trade_id: str,
                    ref_price: float, when: datetime) -> None:
        slip = self.exec.slippage_ticks * self.spec.tick_size
        entry = ref_price + signal.side.sign * slip  # adverse slippage
        stop, target = signal.bracket(entry, self.spec.tick_size)
        self._position = Position(
            side=signal.side, size=size, entry_price=entry,
            stop_price=stop, target_price=target,
            entry_time=when, trade_id=trade_id, peak_price=entry,
        )
        log.info("PAPER entry %s %d @ %.2f | stop %.2f target %.2f (%s)",
                 signal.side.value, size, entry, stop, target,
                 signal.reason)
        if self.on_entry_fill:
            self.on_entry_fill(self._position)

    def _check_exit(self, bar: Bar) -> None:
        pos = self._position
        assert pos is not None
        # mark-to-market for the GUI
        pos.open_pnl = self._pnl_currency(pos, bar.close)

        # Stop/target checked against levels set on prior bars, so the stop
        # manager can't whipsaw out on the same bar that moved it.
        if pos.side is Side.LONG:
            hit_stop = bar.low <= pos.stop_price
            hit_target = bar.high >= pos.target_price
        else:
            hit_stop = bar.high >= pos.stop_price
            hit_target = bar.low <= pos.target_price

        if hit_stop:  # conservative: stop wins a straddle
            self._close(pos.stop_price, bar.end, self._stop_reason(pos))
            return
        if hit_target:
            self._close(pos.target_price, bar.end, "TP")
            return

        # Advance the stop manager from this bar (takes effect next bar).
        if self.exits.trailing_stop_enabled:
            self._manage_stop(pos, bar)

    @staticmethod
    def _stop_reason(pos: Position) -> str:
        if pos.trailing_active:
            return "TS"
        if pos.breakeven_set:
            return "BE"
        return "SL"

    def _manage_stop(self, pos: Position, bar: Bar) -> None:
        """Two-stage stop: break-even at the trigger, then lock peak-profit %.
        Only ever ratchets tighter."""
        sign = pos.side.sign
        if pos.side is Side.LONG:
            pos.peak_price = max(pos.peak_price, bar.high)
        else:
            pos.peak_price = min(pos.peak_price, bar.low)
        peak_profit = (pos.peak_price - pos.entry_price) * sign  # points, >0 in profit
        if peak_profit <= 0:
            log.debug("manage: %s peak %.2f, no open profit yet — stop held %.2f",
                      pos.side.value, pos.peak_price, pos.stop_price)
            return

        target_dist = abs(pos.target_price - pos.entry_price)
        pct_to_target = peak_profit / target_dist * 100.0 if target_dist else 0.0
        log.debug("manage: %s peak %.2f profit %.2f pts (%.0f%% to target) "
                  "stop %.2f [%s]", pos.side.value, pos.peak_price, peak_profit,
                  pct_to_target, pos.stop_price,
                  "TS" if pos.trailing_active else "BE" if pos.breakeven_set else "init")

        # Stage 1: break-even once price travels breakeven_trigger_pct to target.
        if not pos.breakeven_set and target_dist > 0 and \
                peak_profit >= target_dist * self.exits.breakeven_trigger_pct / 100.0:
            buf = _BE_BUFFER_TICKS * self.spec.tick_size
            if self._tighten(pos, pos.entry_price + sign * buf):
                log.info("PAPER stop -> break-even %.2f (reached %.0f%% to target, "
                         "trigger %.0f%%; +%d tick buffer)", pos.stop_price,
                         pct_to_target, self.exits.breakeven_trigger_pct,
                         _BE_BUFFER_TICKS)
            pos.breakeven_set = True

        # Stage 2: after break-even, lock trail_lock_pct of the peak open profit.
        if pos.breakeven_set:
            locked = peak_profit * self.exits.trail_lock_pct / 100.0
            if self._tighten(pos, pos.entry_price + sign * locked):
                pos.trailing_active = True
                log.info("PAPER stop -> trail %.2f (lock %.0f%% of peak profit "
                         "%.2f pts = %.2f pts)", pos.stop_price,
                         self.exits.trail_lock_pct, peak_profit, locked)

    def _tighten(self, pos: Position, candidate: float) -> bool:
        """Move the stop to ``candidate`` only if it is tighter. Returns whether it moved."""
        candidate = self._round_tick(candidate)
        better = (candidate > pos.stop_price if pos.side is Side.LONG
                  else candidate < pos.stop_price)
        if better:
            pos.stop_price = candidate
        return better

    def _round_tick(self, price: float) -> float:
        t = self.spec.tick_size
        return round(round(price / t) * t, 10)

    def _close(self, exit_price: float, when: datetime, reason: str) -> None:
        pos = self._position
        assert pos is not None
        pnl = self._pnl_currency(pos, exit_price)
        risk_per_unit = abs(pos.entry_price - pos.stop_price)
        r = ((exit_price - pos.entry_price) * pos.side.sign / risk_per_unit
             if risk_per_unit else 0.0)
        trade = Trade(
            trade_id=pos.trade_id, mode=self.mode, symbol=self.spec.symbol,
            side=pos.side, size=pos.size, entry_time=pos.entry_time,
            entry_price=pos.entry_price, stop_price=pos.stop_price,
            target_price=pos.target_price, exit_time=when,
            exit_price=exit_price, exit_reason=reason,
            pnl_currency=pnl, r_multiple=r,
        )
        log.info("PAPER exit %s @ %.2f (%s) pnl=$%.2f r=%.2f",
                 pos.side.value, exit_price, reason, pnl, r)
        self._position = None
        if self.on_trade_closed:
            self.on_trade_closed(trade)

    def _pnl_currency(self, pos: Position, price: float) -> float:
        return self._pnl_for(pos, price, pos.size)

    def _pnl_for(self, pos: Position, price: float, size: int) -> float:
        ticks = (price - pos.entry_price) * pos.side.sign / self.spec.tick_size
        gross = ticks * self.spec.tick_value * size
        commission = self.exec.commission_per_side * size * 2
        return gross - commission
