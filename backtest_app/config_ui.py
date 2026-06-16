"""Build AppConfig from Streamlit controls (main page layout)."""
from __future__ import annotations

from datetime import time, timedelta

import streamlit as st

from infra.config import AppConfig, StrategyConfig

TRADE_WINDOW_PRESETS: dict[str, tuple[str, str, bool] | None] = {
    "Full RTH (09:30 – 16:00 ET)": ("09:30", "16:00", False),
    "Morning (09:30 – 12:00 ET)": ("09:30", "12:00", False),
    "Midday (12:00 – 14:00 ET)": ("12:00", "14:00", False),
    "Afternoon (14:00 – 16:00 ET)": ("14:00", "16:00", False),
    "Evening open (18:00 – 21:00 ET)": ("18:00", "21:00", False),
    "Overnight / Asian (20:00 – 03:00 ET)": ("20:00", "03:00", True),
    "Custom times": None,
}

HINTS = {
    "ema_fast": "Fast exponential moving average period. Trend is up when EMA fast > EMA slow.",
    "ema_slow": "Slow EMA period. Must be greater than EMA fast.",
    "atr_period": "Average True Range lookback for strong-candle and spike guards.",
    "require_vwap": "When on, longs need close above session VWAP; shorts need close below.",
    "vwap_anchor": "rth = VWAP resets at 09:30 ET. globex = resets at 18:00 ET session open.",
    "entry_momentum": "Enter on a strong continuation candle in the trend direction.",
    "entry_break": "Enter when price breaks the previous bar high (long) or low (short).",
    "body_ratio": "Minimum body size as a fraction of the bar range (0–1).",
    "strong_atr": "Bar range must be at least this multiple of ATR to qualify as strong.",
    "spike": "Reject if bar range exceeds this multiple of ATR (overextended).",
    "chop": "Reject if EMAs are closer than this multiple of ATR (choppy market).",
    "near_close": "Block new entries this many minutes before session/trade end.",
    "risk_mode": "dynamic = swing/candle stop + R-multiple target. fixed = fixed tick stop/TP.",
    "stop_mode": "swing = stop at recent swing low/high. candle = stop at signal bar extreme.",
    "swing_lb": "Bars looked back for swing extreme when stop_mode is swing.",
    "tick_buf": "Extra ticks padded beyond the structure stop.",
    "tp_ratio": "Target distance as a multiple of entry-to-stop risk (dynamic mode).",
    "timeframe": "Bar size in minutes. Client requested NQ 1-minute.",
    "extended": "Turn on for overnight windows that cross midnight (e.g. Asian 20:00–03:00).",
    "slippage": "Adverse slippage applied on entry fill (in ticks).",
    "contracts": "Number of NQ contracts per trade ($20/point each).",
    "daily_loss": "TopStep daily halt: no more trades after this loss in one session (resets next day).",
    "consec_loss": "TopStep halt: stop trading for the day after this many losses in a row (resets next day).",
    "trailing_dd": "Account-wide guard: blocks new entries when equity is near the trailing drawdown floor. "
                   "Disabled by default in backtests — use TopStep daily halts for day-level prop-firm limits.",
    "flatten": "Live bot stops new entries at flatten time (5 min before trade end). Open positions flatten at session close.",
    "topstep_halts": "TopStep daily rules: halt new entries for the rest of the session after hitting the "
                     "daily loss limit or max consecutive losses. Resets next trading day. Turn OFF to compare "
                     "full strategy activity vs prop-firm halts.",
    "trailing_stop": "Two-stage exit manager on open trades. Stage 1 — break-even: when price reaches "
                     "breakeven_trigger_pct (10%) of the way to target, stop moves to entry (+ fee buffer). "
                     "Stage 2 — profit lock: after break-even, trail the stop to lock trail_lock_pct (50%) "
                     "of peak open profit. Stop ratchets up only; full exit when trailing stop is hit.",
}


def _fmt_hhmm(t: time) -> str:
    return t.strftime("%H:%M")


def _flatten_before_end(end: time, minutes: int = 5) -> str:
    total = int((timedelta(hours=end.hour, minutes=end.minute) - timedelta(minutes=minutes)).total_seconds())
    h, rem = divmod(max(0, total), 3600)
    return f"{h:02d}:{rem // 60:02d}"


def render_trade_window_controls(live_bot_flatten: bool = True) -> tuple[str, str, str, bool]:
    """Return (start_et, end_et, flatten_et, extended_hours)."""
    preset = st.selectbox(
        "Intraday trade window preset",
        list(TRADE_WINDOW_PRESETS.keys()),
        index=0,
        help="Test morning vs afternoon vs overnight. Only **new entries** are allowed inside this window each day (ET). Indicators still update on all bars.",
    )
    entry = TRADE_WINDOW_PRESETS[preset]
    extended = False

    if entry is None:
        c1, c2, c3 = st.columns(3)
        with c1:
            trade_start = st.time_input("Trade start (ET)", time(9, 30), step=timedelta(minutes=1), key="tw_s")
        with c2:
            trade_end = st.time_input("Trade end (ET)", time(16, 0), step=timedelta(minutes=1), key="tw_e")
        with c3:
            extended = st.checkbox("Crosses midnight", value=False, help=HINTS["extended"])
    else:
        start_s, end_s, extended = entry
        parts_start = start_s.split(":")
        parts_end = end_s.split(":")
        trade_start = time(int(parts_start[0]), int(parts_start[1]))
        trade_end = time(int(parts_end[0]), int(parts_end[1]))
        st.caption(f"**{preset}** · entries {_fmt_hhmm(trade_start)} – {_fmt_hhmm(trade_end)} ET")

    if not extended and trade_start >= trade_end:
        st.error("Trade start must be before trade end (or enable crosses-midnight for overnight).")

    flatten = (
        _flatten_before_end(trade_end, minutes=5)
        if live_bot_flatten
        else _fmt_hhmm(trade_end)
    )
    if live_bot_flatten:
        st.caption(f"Flatten new entries at **{flatten} ET** ({HINTS['flatten']})")
    else:
        st.caption(f"Entries allowed until **{_fmt_hhmm(trade_end)} ET** (Pine / full-session mode — no early flatten).")
    return _fmt_hhmm(trade_start), _fmt_hhmm(trade_end), flatten, extended


def render_strategy_config(base: AppConfig) -> tuple[AppConfig, bool]:
    cfg = base.model_copy(deep=True)
    s = cfg.strategy

    st.markdown('<div class="config-nav">', unsafe_allow_html=True)

    topstep_halts = st.checkbox(
        "Enable TopStep daily halts (live bot mode)",
        value=True,
        key="topstep_daily_halts",
        help=HINTS["topstep_halts"],
    )
    if topstep_halts:
        st.caption("Live bot: daily loss / consecutive-loss halts ON · entries stop at **15:55** flatten.")
    else:
        st.caption("Pine-style session: halts OFF · entries until **trade end (16:00)** — closer to TradingView backtests.")

    t1, t2, t3 = st.tabs(["Strategy", "Market & session", "Risk & execution"])

    with t1:
        c1, c2, c3 = st.columns(3)
        s.ema_fast = c1.number_input("EMA fast", 1, 200, s.ema_fast, help=HINTS["ema_fast"])
        s.ema_slow = c2.number_input("EMA slow", 2, 400, s.ema_slow, help=HINTS["ema_slow"])
        s.atr_period = c3.number_input("ATR period", 1, 100, s.atr_period, help=HINTS["atr_period"])
        s.require_vwap = st.checkbox("Require VWAP side for trend", s.require_vwap, help=HINTS["require_vwap"])

        st.markdown("**Entry triggers**")
        c1, c2, c3, c4 = st.columns(4)
        s.entry_on_momentum_candle = c1.checkbox("Momentum candle", s.entry_on_momentum_candle, help=HINTS["entry_momentum"])
        s.entry_on_prev_break = c2.checkbox("Prev high/low break", s.entry_on_prev_break, help=HINTS["entry_break"])
        s.strong_candle_body_ratio = c3.number_input("Body ratio", 0.0, 1.0, float(s.strong_candle_body_ratio), 0.05, help=HINTS["body_ratio"])
        s.strong_candle_atr_mult = c4.number_input("Strong ATR mult", 0.0, 5.0, float(s.strong_candle_atr_mult), 0.1, help=HINTS["strong_atr"])

        st.markdown("**Guards**")
        c1, c2, c3 = st.columns(3)
        s.spike_atr_mult = c1.number_input("Spike ATR mult", 0.1, 10.0, float(s.spike_atr_mult), 0.1, help=HINTS["spike"])
        s.chop_ema_atr_mult = c2.number_input("Chop EMA ATR mult", 0.0, 2.0, float(s.chop_ema_atr_mult), 0.05, help=HINTS["chop"])
        s.no_entry_minutes_before_close = c3.number_input("No entry before close (min)", 0, 120, s.no_entry_minutes_before_close, help=HINTS["near_close"])

        st.markdown("**Stop & target**")
        c1, c2 = st.columns([1, 3])
        s.risk_mode = c1.selectbox("Risk mode", ["dynamic", "fixed"], index=0 if s.risk_mode == "dynamic" else 1, help=HINTS["risk_mode"])
        if s.risk_mode == "dynamic":
            c1, c2, c3, c4 = st.columns(4)
            s.stop_mode = c1.selectbox("Stop mode", ["swing", "candle"], index=0 if s.stop_mode == "swing" else 1, help=HINTS["stop_mode"])
            s.stop_swing_lookback = c2.number_input("Swing lookback", 1, 50, s.stop_swing_lookback, help=HINTS["swing_lb"])
            s.tick_buffer = c3.number_input("Tick buffer", 0, 20, s.tick_buffer, help=HINTS["tick_buf"])
            s.tp_ratio = c4.number_input("TP ratio (R)", 0.5, 10.0, float(s.tp_ratio), 0.1, help=HINTS["tp_ratio"])
        else:
            c1, c2 = st.columns(2)
            s.fixed_stop_ticks = c1.number_input("Fixed stop (ticks)", 1, 500, s.fixed_stop_ticks)
            s.fixed_tp_ticks = c2.number_input("Fixed TP (ticks)", 1, 500, s.fixed_tp_ticks)

        c1, c2 = st.columns(2)
        s.enable_long = c1.checkbox("Enable long", s.enable_long)
        s.enable_short = c2.checkbox("Enable short", s.enable_short)

    with t2:
        c1, c2, c3 = st.columns(3)
        cfg.market.instrument = c1.selectbox("Instrument", ["NQ", "MNQ"], index=0 if cfg.market.instrument == "NQ" else 1, help="NQ = $20/point. MNQ = $2/point.")
        tf_opts = [1, 2, 3, 5, 15]
        default_tf = 1 if cfg.market.timeframe_minutes not in tf_opts else cfg.market.timeframe_minutes
        cfg.market.timeframe_minutes = c2.selectbox(
            "Bar timeframe (min)",
            tf_opts,
            index=tf_opts.index(default_tf),
            help=HINTS["timeframe"],
        )
        cfg.market.vwap_anchor = c3.selectbox("VWAP anchor", ["rth", "globex"], index=0 if cfg.market.vwap_anchor == "rth" else 1, help=HINTS["vwap_anchor"])

        trade_start, trade_end, flatten_et, preset_extended = render_trade_window_controls(
            live_bot_flatten=topstep_halts,
        )
        cfg.market.extended_hours = st.checkbox(
            "Extended (overnight) session clock",
            value=preset_extended,
            help="Match live bot: ON uses Globex 18:00–17:00 ET session for entries. "
                 "Leave OFF when testing RTH-only presets (09:30–16:00).",
        )
        if cfg.market.extended_hours:
            cfg.market.extended_window.start_et = trade_start
            cfg.market.extended_window.end_et = trade_end
            cfg.market.extended_flatten_et = flatten_et
        else:
            cfg.market.trading_window.start_et = trade_start
            cfg.market.trading_window.end_et = trade_end
            cfg.market.flatten_et = flatten_et

    with t3:
        c1, c2, c3, c4 = st.columns(4)
        cfg.risk.contracts_per_trade = c1.number_input("Contracts / trade", 1, 20, cfg.risk.contracts_per_trade, help=HINTS["contracts"])
        cfg.risk.max_trades_per_day = c2.number_input("Max trades / day", 1, 100, cfg.risk.max_trades_per_day)
        max_consec = c3.number_input(
            "Max consecutive losses",
            1,
            20,
            cfg.risk.max_consecutive_losses,
            disabled=not topstep_halts,
            help=HINTS["consec_loss"],
        )
        daily_loss = c4.number_input(
            "Daily loss limit ($)",
            0.0,
            50_000.0,
            float(cfg.risk.daily_loss_limit_currency),
            100.0,
            disabled=not topstep_halts,
            help=HINTS["daily_loss"],
        )
        cfg.risk.max_consecutive_losses = max_consec if topstep_halts else 999
        cfg.risk.daily_loss_limit_currency = daily_loss if topstep_halts else 999_999.0

        c1, c2, c3 = st.columns(3)
        cfg.execution.slippage_ticks = c1.number_input("Slippage (ticks)", 0, 10, cfg.execution.slippage_ticks, help=HINTS["slippage"])
        cfg.execution.commission_per_side = c2.number_input("Commission / side ($)", 0.0, 50.0, float(cfg.execution.commission_per_side), 0.5)
        cfg.exits.trailing_stop_enabled = c3.checkbox(
            "Trailing stop manager",
            cfg.exits.trailing_stop_enabled,
            help=HINTS["trailing_stop"],
        )
        # Trailing drawdown guard is kept off in the backtester — it blocks entries for the
        # entire replay (not just one day) and overlaps confusingly with TopStep daily halts.
        cfg.risk.trailing_drawdown_guard_enabled = False

    if s.ema_slow <= s.ema_fast:
        st.error("EMA slow must be greater than EMA fast.")
    cfg.strategy = StrategyConfig(**s.model_dump())
    st.markdown("</div>", unsafe_allow_html=True)
    return cfg, topstep_halts
