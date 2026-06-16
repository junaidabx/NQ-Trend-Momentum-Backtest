"""Trend + momentum-continuation strategy (long + short).

Rules:
  * Trend direction from the EMA relationship: long when EMA_fast > EMA_slow,
    short when EMA_fast < EMA_slow. ``require_vwap`` adds a close-vs-VWAP filter.
  * Entry trigger (in the trend direction), either: a strong continuation candle
    (large body fraction of range and range large vs ATR, closing in-trend), or
    a break of the previous candle's high/low in the trend direction.
  * Reject (and log) a trigger when the market is chopping (EMAs within
    ``chop_ema_atr_mult`` x ATR), when the trigger bar or the one before it is an
    overextended spike (range > ``spike_atr_mult`` x ATR), or within
    ``no_entry_minutes_before_close`` minutes of the close.
  * Bracket (``risk_mode``, mutually exclusive):
      - dynamic: stop at the swing extreme over ``stop_swing_lookback`` bars (or
        the trigger candle's extreme when ``stop_mode="candle"``) padded by a
        tick buffer; target at ``tp_ratio`` x risk from the real fill.
      - fixed: stop/target a fixed tick distance (``fixed_stop_ticks`` /
        ``fixed_tp_ticks``) either side of the real fill.
    Both brackets are computed off the actual fill by the broker.

A signal fires on the bar whose trigger confirms; the fill is next-bar-open.
Every closed bar is evaluated, so continuation entries can fire across the
session. Blocked triggers are logged at INFO with a REJECT tag and counted in
:attr:`rejections`.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from infra.config import MarketConfig, StrategyConfig

from .bars import Bar
from .indicators import ATR, EMA, SessionVWAP
from .state import Side, Signal, utcnow

log = logging.getLogger("strategy")

_ET = ZoneInfo("America/New_York")
_GLOBEX_OPEN = time(18, 0)          # CME electronic session opens 18:00 ET
_DEFAULT_RTH_OPEN = time(9, 30)
_DEFAULT_RTH_CLOSE = time(16, 0)


def _parse_hhmm(value: str) -> time:
    hh, mm = value.split(":")
    return time(int(hh), int(mm))


@dataclass
class _Trigger:
    side: Side
    label: str


class TrendMomentumStrategy:
    def __init__(self, config: StrategyConfig, tick_size: float,
                 market: Optional[MarketConfig] = None):
        self.cfg = config
        self.tick_size = tick_size
        self.ema_fast = EMA(config.ema_fast)
        self.ema_slow = EMA(config.ema_slow)
        self.atr = ATR(config.atr_period)
        self.vwap = SessionVWAP()
        self.bars: list[Bar] = []
        # rejection tally by reason, for diagnostics / the GUI
        self.rejections: dict[str, int] = {}

        # VWAP anchor: "rth" anchors at the RTH open (09:30 ET), "globex" at the
        # Globex open (18:00 ET). Defaults to RTH when no market config (tests).
        self._vwap_anchor = market.vwap_anchor if market else "rth"
        self._rth_open = (_parse_hhmm(market.trading_window.start_et)
                          if market else _DEFAULT_RTH_OPEN)
        self._rth_close = (_parse_hhmm(market.trading_window.end_et)
                           if market else _DEFAULT_RTH_CLOSE)
        # close the near-close guard counts down to: extended-session end when
        # extended hours are on (may be next calendar day), else the RTH close
        self._extended = bool(market.extended_hours) if market else False
        self._session_close = (_parse_hhmm(market.extended_window.end_et)
                               if (self._extended and market) else self._rth_close)
        self._vwap_key: Optional[str] = None

    def apply_market(self, market: MarketConfig) -> None:
        """Re-read session fields after a live config change.

        Keeps VWAP anchoring and the near-close reference in sync when the user
        flips extended hours or the windows without a restart.
        """
        self._vwap_anchor = market.vwap_anchor
        self._rth_open = _parse_hhmm(market.trading_window.start_et)
        self._rth_close = _parse_hhmm(market.trading_window.end_et)
        self._extended = bool(market.extended_hours)
        self._session_close = (_parse_hhmm(market.extended_window.end_et)
                               if self._extended else self._rth_close)

    # session lifecycle
    def reset_session(self) -> None:
        """Clear the VWAP key at a session boundary; _feed_vwap re-anchors."""
        self.vwap.reset()
        self._vwap_key = None

    def _feed_vwap(self, bar: Bar) -> None:
        """Update session VWAP per the configured anchor.

        rth: anchor at the RTH open; only RTH bars contribute and VWAP
        re-anchors on each new RTH date. globex: anchor at the 18:00 ET open;
        all bars contribute and the session rolls at 18:00 ET.
        """
        et_dt = bar.start.astimezone(_ET)
        t = et_dt.time()
        if self._vwap_anchor == "rth":
            if not (self._rth_open <= t < self._rth_close):
                return  # outside RTH: don't pollute the RTH-anchored VWAP
            key = et_dt.strftime("%Y-%m-%d")
        else:  # globex: a session opening at 18:00 ET belongs to the next date
            sess_date = et_dt.date()
            if t >= _GLOBEX_OPEN:
                sess_date = (et_dt + timedelta(days=1)).date()
            key = sess_date.isoformat()
        if key != self._vwap_key:
            self.vwap.reset()
            self._vwap_key = key
        self.vwap.update(bar)

    def prime(self, bars: list[Bar]) -> None:
        """Warm indicators from backfilled history."""
        for bar in bars:
            self.ema_fast.update(bar.close)
            self.ema_slow.update(bar.close)
            self.atr.update(bar)
            self._feed_vwap(bar)
            self.bars.append(bar)

    @property
    def is_warm(self) -> bool:
        return (self.ema_fast.is_warm and self.ema_slow.is_warm
                and self.atr.is_warm and self.vwap.value is not None)

    # main entry point
    def on_bar(self, bar: Bar, in_position: bool) -> Optional[Signal]:
        """Fold a closed bar in and return a Signal if an entry triggers."""
        self.ema_fast.update(bar.close)
        self.ema_slow.update(bar.close)
        self.atr.update(bar)
        self._feed_vwap(bar)
        self.bars.append(bar)

        if in_position:
            return None
        if not self.is_warm:
            log.debug("EVAL skip: indicators warming "
                      "(ema_fast=%s ema_slow=%s atr=%s vwap=%s)",
                      self.ema_fast.is_warm, self.ema_slow.is_warm,
                      self.atr.is_warm, self.vwap.value is not None)
            return None

        ef, es = self.ema_fast.value, self.ema_slow.value
        atr, vw = self.atr.value, self.vwap.value
        assert ef is not None and es is not None and atr is not None and vw is not None

        # full per-bar condition trace at DEBUG
        trend = "up" if ef > es else "down" if ef < es else "flat"
        log.debug("EVAL %s | close %.2f ema_fast %.2f ema_slow %.2f (Δ%+.2f) "
                  "atr %.2f vwap %.2f", trend, bar.close, ef, es, ef - es, atr, vw)

        # trend direction from the EMA relationship (+ optional VWAP side)
        up = ef > es and (not self.cfg.require_vwap or bar.close > vw)
        down = ef < es and (not self.cfg.require_vwap or bar.close < vw)
        if up and self.cfg.enable_long:
            side = Side.LONG
        elif down and self.cfg.enable_short:
            side = Side.SHORT
        else:
            log.debug("no candidate: %s", self._no_candidate_reason(ef, es, vw, bar))
            return None  # no trend in an enabled direction

        # momentum-continuation trigger in the trend direction
        trigger = self._trigger(bar, atr, side)
        if trigger is None:
            log.debug("%s trend, no entry trigger (%s)", side.value,
                      self._no_trigger_reason(bar, atr, side))
            return None  # in trend but no trigger; nothing to reject

        log.debug("%s trigger: %s — checking avoidance guards",
                  side.value, trigger.label)

        # avoidance guards: reject (and log) the candidate if any trips
        if abs(ef - es) < self.cfg.chop_ema_atr_mult * atr:
            return self._reject(side, "chop",
                                f"EMAΔ {abs(ef - es):.2f} < "
                                f"{self.cfg.chop_ema_atr_mult}xATR {atr:.2f} "
                                f"({trigger.label})")
        if self._spike(bar, atr) or (len(self.bars) >= 2
                                     and self._spike(self.bars[-2], atr)):
            return self._reject(side, "overextended-spike",
                                f"range > {self.cfg.spike_atr_mult}xATR {atr:.2f} "
                                f"({trigger.label})")
        mins = self._minutes_to_close(bar)
        if mins < self.cfg.no_entry_minutes_before_close:
            return self._reject(side, "near-close",
                                f"{mins:.0f}m to close < "
                                f"{self.cfg.no_entry_minutes_before_close}m "
                                f"({trigger.label})")

        return self._fire(side, bar, trigger.label)

    # triggers
    def _trigger(self, bar: Bar, atr: float, side: Side) -> Optional[_Trigger]:
        prev = self.bars[-2] if len(self.bars) >= 2 else None
        if self.cfg.entry_on_momentum_candle and self._is_strong(bar, atr, side):
            return _Trigger(side, "momentum candle")
        if self.cfg.entry_on_prev_break and prev is not None:
            if side is Side.LONG and bar.high > prev.high and bar.close > prev.close:
                return _Trigger(side, "prev-high break")
            if side is Side.SHORT and bar.low < prev.low and bar.close < prev.close:
                return _Trigger(side, "prev-low break")
        return None

    def _is_strong(self, bar: Bar, atr: float, side: Side) -> bool:
        rng = bar.high - bar.low
        if rng <= 0:
            return False
        body = abs(bar.close - bar.open)
        if body / rng < self.cfg.strong_candle_body_ratio:
            return False
        if rng < self.cfg.strong_candle_atr_mult * atr:
            return False
        return bar.is_bullish() if side is Side.LONG else bar.is_bearish()

    def _spike(self, bar: Bar, atr: float) -> bool:
        return (bar.high - bar.low) > self.cfg.spike_atr_mult * atr

    # diagnostics (DEBUG explanations for "nothing fired")
    def _no_candidate_reason(self, ef: float, es: float, vw: float,
                             bar: Bar) -> str:
        """Explain why an in-trend candidate did not form."""
        if ef > es and not self.cfg.enable_long:
            return "up-trend but longs disabled (enable_long=false)"
        if ef < es and not self.cfg.enable_short:
            return "down-trend but shorts disabled (enable_short=false)"
        if self.cfg.require_vwap and ef > es and bar.close <= vw:
            return (f"up-trend but close {bar.close:.2f} <= vwap {vw:.2f} "
                    f"(require_vwap)")
        if self.cfg.require_vwap and ef < es and bar.close >= vw:
            return (f"down-trend but close {bar.close:.2f} >= vwap {vw:.2f} "
                    f"(require_vwap)")
        return "no directional EMA trend (ema_fast == ema_slow)"

    def _no_trigger_reason(self, bar: Bar, atr: float, side: Side) -> str:
        """Explain why no momentum/break trigger formed in the trend direction."""
        parts: list[str] = []
        if self.cfg.entry_on_momentum_candle:
            parts.append(f"momentum candle {self._strong_detail(bar, atr, side)}")
        if self.cfg.entry_on_prev_break:
            parts.append("no prev-high/low break in trend")
        return "; ".join(parts) if parts else "no entry-trigger mode enabled"

    def _strong_detail(self, bar: Bar, atr: float, side: Side) -> str:
        """Which strong-candle condition failed (or 'ok')."""
        rng = bar.high - bar.low
        if rng <= 0:
            return "rejected (zero-range bar)"
        body = abs(bar.close - bar.open)
        if body / rng < self.cfg.strong_candle_body_ratio:
            return (f"rejected (body {body / rng:.0%} < "
                    f"{self.cfg.strong_candle_body_ratio:.0%} of range)")
        if rng < self.cfg.strong_candle_atr_mult * atr:
            return (f"rejected (range {rng:.2f} < {self.cfg.strong_candle_atr_mult}"
                    f"xATR = {self.cfg.strong_candle_atr_mult * atr:.2f})")
        dir_ok = bar.is_bullish() if side is Side.LONG else bar.is_bearish()
        if not dir_ok:
            return "rejected (closed against trend)"
        return "ok"

    def _minutes_to_close(self, bar: Bar) -> float:
        et_dt = bar.start.astimezone(_ET)
        if self._extended:
            close_dt = datetime.combine(et_dt.date(), self._session_close, tzinfo=_ET)
            if close_dt <= et_dt:
                close_dt += timedelta(days=1)
            return (close_dt - et_dt).total_seconds() / 60.0
        # RTH: Pine-style wrap so overnight bars are not falsely "near close"
        t_min = et_dt.hour * 60 + et_dt.minute
        close_min = self._session_close.hour * 60 + self._session_close.minute
        if close_min > t_min:
            return float(close_min - t_min)
        return float((24 * 60 - t_min) + close_min)

    # stop / fire
    def _stop_for(self, side: Side, bar: Bar) -> tuple[float, float]:
        """Return ``(stop_price, structure_extreme)`` for the dynamic stop."""
        buf = self.cfg.tick_buffer * self.tick_size
        if self.cfg.stop_mode == "candle":
            ext = bar.low if side is Side.LONG else bar.high
        else:  # swing extreme over the recent lookback
            window = self.bars[-self.cfg.stop_swing_lookback:]
            ext = (min(b.low for b in window) if side is Side.LONG
                   else max(b.high for b in window))
        stop = ext - buf if side is Side.LONG else ext + buf
        return stop, ext

    def _fire(self, side: Side, bar: Bar, trigger: str) -> Signal:
        ref = bar.close  # provisional; fill is next-bar-open
        if self.cfg.risk_mode == "fixed":
            return self._fire_fixed(side, bar, trigger, ref)
        stop, ext = self._stop_for(side, bar)
        sig = Signal(side, stop, self.cfg.tp_ratio, utcnow(),
                     f"{side.value.upper()} {trigger}; stop {stop:.2f} "
                     f"({self.cfg.stop_mode} {ext:.2f})",
                     ref_price=ref)
        log.info("SIGNAL %s | %s | ref %.2f stop %.2f tp %.1fR",
                 side.value, trigger, ref, stop, self.cfg.tp_ratio)
        return sig

    def _fire_fixed(self, side: Side, bar: Bar, trigger: str, ref: float) -> Signal:
        """Fixed-tick bracket: stop/target a set tick distance from the fill.
        The broker re-derives both from the real fill; the provisional stop here
        keeps the engine's risk projection correct (= fixed_stop_ticks)."""
        st, tt = self.cfg.fixed_stop_ticks, self.cfg.fixed_tp_ticks
        stop = ref - side.sign * st * self.tick_size
        sig = Signal(side, stop, self.cfg.tp_ratio, utcnow(),
                     f"{side.value.upper()} {trigger}; fixed stop {st}t / tp {tt}t",
                     ref_price=ref, fixed_stop_ticks=st, fixed_tp_ticks=tt)
        log.info("SIGNAL %s | %s | ref %.2f stop %dt tp %dt (fixed)",
                 side.value, trigger, ref, st, tt)
        return sig

    # rejection logging
    def _reject(self, side: Side, reason: str, detail: str) -> None:
        self.rejections[reason] = self.rejections.get(reason, 0) + 1
        log.info("REJECT %s | %s - %s", side.value, reason, detail)
        return None
