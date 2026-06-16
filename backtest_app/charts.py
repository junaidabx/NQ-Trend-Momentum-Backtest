"""Chart helpers for the Streamlit backtest dashboard."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from core.state import Side, Trade

_ET = ZoneInfo("America/New_York")

# Streamlit plotly: mouse-wheel zoom + click-drag pan (toolbar has zoom box too).
PLOTLY_UI = {
    "scrollZoom": True,
    "displayModeBar": True,
    "displaylogo": False,
    "responsive": True,
    "modeBarButtonsToRemove": ["lasso2d", "select2d"],
}

# Full-width price chart — responsive height via CSS + autosize.
PRICE_CHART_UI = {**PLOTLY_UI, "responsive": True}
PRICE_CHART_HEIGHT = 580
# Bars actually drawn (pan within this window). Keeps chart light vs full history.
CHART_MAX_RENDER_BARS = 480
CHART_MIN_RENDER_BARS = 96
CHART_RENDER_MULT = 16


def _apply_chart_interaction(fig: go.Figure) -> go.Figure:
    fig.update_layout(dragmode="pan")
    return fig

# TradingView-style marker colours
_CLR_LONG_ENTRY = "#26a69a"
_CLR_LONG_EXIT = "#e040fb"
_CLR_SHORT_ENTRY = "#e040fb"
_CLR_SHORT_EXIT = "#42a5f5"
_CLR_SIGNAL = "#fbbf24"
_CLR_POS_FILL = "rgba(38,166,154,0.08)"
_CLR_NEG_FILL = "rgba(224,64,251,0.08)"


def bars_to_df(bars) -> pd.DataFrame:
    rows = []
    for b in bars:
        rows.append(
            {
                "time": b.start,
                "open": b.open,
                "high": b.high,
                "low": b.low,
                "close": b.close,
                "volume": b.volume,
            }
        )
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["time_et"] = df["time"].dt.tz_convert(_ET)
    return df


def add_indicators(
    df: pd.DataFrame,
    ema_fast: int,
    ema_slow: int,
    atr_period: int = 14,
) -> pd.DataFrame:
    out = df.copy()
    out["ema_fast"] = out["close"].ewm(span=ema_fast, adjust=False).mean()
    out["ema_slow"] = out["close"].ewm(span=ema_slow, adjust=False).mean()
    out["ema_delta"] = out["ema_fast"] - out["ema_slow"]
    out["bar_range"] = out["high"] - out["low"]
    out["body"] = (out["close"] - out["open"]).abs()
    out["body_ratio"] = out["body"] / out["bar_range"].where(out["bar_range"] > 0, pd.NA)
    out["atr"] = _wilder_atr(out, atr_period)
    out["range_atr"] = out["bar_range"] / out["atr"].where(out["atr"] > 0, pd.NA)
    return out


def _wilder_atr(df: pd.DataFrame, period: int) -> pd.Series:
    """Wilder ATR — matches engine/core/indicators.ATR."""
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = pd.Series(index=df.index, dtype=float)
    if len(df) < period:
        return atr
    seed = tr.iloc[:period].mean()
    atr.iloc[period - 1] = seed
    value = seed
    for i in range(period, len(df)):
        value = (value * (period - 1) + tr.iloc[i]) / period
        atr.iloc[i] = value
    return atr


@dataclass(frozen=True)
class ChartDiagnostics:
    """Strategy thresholds for signal-bar validation on the chart."""

    atr_period: int = 14
    strong_candle_body_ratio: float = 0.5
    strong_candle_atr_mult: float = 0.8
    spike_atr_mult: float = 2.0
    chop_ema_atr_mult: float = 0.25
    entry_on_momentum_candle: bool = True
    entry_on_prev_break: bool = True


def _entry_bar_index(df: pd.DataFrame, entry_time: datetime) -> int | None:
    if df.empty:
        return None
    times = df["time"]
    match = df.index[times == entry_time].tolist()
    if match:
        return int(match[0])
    idx = int(times.searchsorted(entry_time, side="left"))
    if idx >= len(df):
        idx = len(df) - 1
    return idx


def _signal_bar_index(df: pd.DataFrame, entry_time: datetime) -> int | None:
    """Signal bar = bar before next-open fill (strategy trigger candle)."""
    idx = _entry_bar_index(df, entry_time)
    if idx is None or idx <= 0:
        return None
    return idx - 1


def _is_strong_candle(row: pd.Series, atr: float, side: Side, diag: ChartDiagnostics) -> bool:
    rng = float(row["bar_range"])
    if rng <= 0 or atr <= 0:
        return False
    body_ratio = float(row["body_ratio"]) if pd.notna(row["body_ratio"]) else 0.0
    if body_ratio < diag.strong_candle_body_ratio:
        return False
    if rng < diag.strong_candle_atr_mult * atr:
        return False
    bullish = float(row["close"]) > float(row["open"])
    return bullish if side is Side.LONG else not bullish


def _is_prev_break(row: pd.Series, prev: pd.Series, side: Side) -> bool:
    if side is Side.LONG:
        return float(row["high"]) > float(prev["high"]) and float(row["close"]) > float(prev["close"])
    return float(row["low"]) < float(prev["low"]) and float(row["close"]) < float(prev["close"])


def _infer_trigger(row: pd.Series, prev: pd.Series | None, side: Side, diag: ChartDiagnostics) -> str:
    atr = float(row["atr"]) if pd.notna(row["atr"]) else 0.0
    if diag.entry_on_momentum_candle and _is_strong_candle(row, atr, side, diag):
        return "momentum candle"
    if diag.entry_on_prev_break and prev is not None and _is_prev_break(row, prev, side):
        return "prev-high break" if side is Side.LONG else "prev-low break"
    return "unknown"


def _analyze_signal_bar(
    df: pd.DataFrame,
    trade: Trade,
    diag: ChartDiagnostics,
) -> dict | None:
    sig_idx = _signal_bar_index(df, trade.entry_time)
    if sig_idx is None:
        return None
    row = df.iloc[sig_idx]
    prev = df.iloc[sig_idx - 1] if sig_idx > 0 else None
    atr = float(row["atr"]) if pd.notna(row["atr"]) else 0.0
    rng = float(row["bar_range"])
    body_ratio = float(row["body_ratio"]) if pd.notna(row["body_ratio"]) else 0.0
    range_atr = float(row["range_atr"]) if pd.notna(row["range_atr"]) else 0.0
    ema_delta = float(row["ema_delta"]) if pd.notna(row["ema_delta"]) else 0.0
    side = trade.side

    body_ok = body_ratio >= diag.strong_candle_body_ratio if rng > 0 else False
    range_ok = rng >= diag.strong_candle_atr_mult * atr if atr > 0 else False
    spike = rng > diag.spike_atr_mult * atr if atr > 0 else False
    prev_spike = False
    if prev is not None and atr > 0:
        prev_rng = float(prev["high"]) - float(prev["low"])
        prev_spike = prev_rng > diag.spike_atr_mult * atr
    chop = abs(ema_delta) < diag.chop_ema_atr_mult * atr if atr > 0 else False
    trigger = _infer_trigger(row, prev, side, diag)
    momentum_ok = _is_strong_candle(row, atr, side, diag)
    break_ok = prev is not None and _is_prev_break(row, prev, side)

    return {
        "sig_idx": sig_idx,
        "time_et": row["time_et"],
        "open": float(row["open"]),
        "high": float(row["high"]),
        "low": float(row["low"]),
        "close": float(row["close"]),
        "body": float(row["body"]),
        "bar_range": rng,
        "body_ratio": body_ratio,
        "atr": atr,
        "range_atr": range_atr,
        "ema_delta": ema_delta,
        "trigger": trigger,
        "body_ok": body_ok,
        "range_ok": range_ok,
        "momentum_ok": momentum_ok,
        "break_ok": break_ok,
        "spike": spike,
        "prev_spike": prev_spike,
        "chop": chop,
        "valid": trigger != "unknown" and not spike and not prev_spike and not chop,
    }


def _signal_hover_html(info: dict, trade: Trade, diag: ChartDiagnostics) -> str:
    side = trade.side.value.upper()
    body_pct = info["body_ratio"] * 100
    lines = [
        f"<b>Signal bar ({side})</b> · {info['trigger']}",
        f"O/H/L/C: {info['open']:.2f} / {info['high']:.2f} / {info['low']:.2f} / {info['close']:.2f}",
        f"Body: {info['body']:.2f} · Range: {info['bar_range']:.2f}",
        f"Body/Range: {body_pct:.0f}% (need ≥ {diag.strong_candle_body_ratio * 100:.0f}%)",
        f"ATR({diag.atr_period}): {info['atr']:.2f}",
        f"Range/ATR: {info['range_atr']:.2f} (need ≥ {diag.strong_candle_atr_mult:.2f})",
        f"EMAΔ: {info['ema_delta']:+.2f} (chop if |Δ| < {diag.chop_ema_atr_mult:.2f}×ATR)",
        f"Momentum OK: {'✓' if info['momentum_ok'] else '✗'} · Break OK: {'✓' if info['break_ok'] else '✗'}",
        f"Spike bar: {'✗' if info['spike'] else '✓'} · Spike prev: {'✗' if info['prev_spike'] else '✓'} · Chop: {'✗' if info['chop'] else '✓'}",
        f"Fill next bar @ {trade.entry_price:.2f}",
    ]
    return "<br>".join(lines)


def signal_diagnostics_dataframe(
    df: pd.DataFrame,
    trades: list[Trade],
    diag: ChartDiagnostics,
) -> pd.DataFrame:
    rows = []
    for trade in trades:
        info = _analyze_signal_bar(df, trade, diag)
        if info is None:
            continue
        rows.append(
            {
                "entry_et": trade.entry_time.astimezone(_ET).strftime("%Y-%m-%d %H:%M"),
                "side": trade.side.value.upper(),
                "trigger": info["trigger"],
                "body_pct": round(info["body_ratio"] * 100, 1),
                "body_min_pct": round(diag.strong_candle_body_ratio * 100, 1),
                "range": round(info["bar_range"], 2),
                "atr": round(info["atr"], 2),
                "range_atr": round(info["range_atr"], 2),
                "range_min_atr": diag.strong_candle_atr_mult,
                "momentum_ok": info["momentum_ok"],
                "break_ok": info["break_ok"],
                "spike": info["spike"],
                "prev_spike": info["prev_spike"],
                "chop": info["chop"],
                "checks_ok": info["valid"],
                "pnl": round(trade.pnl_currency, 2),
            }
        )
    return pd.DataFrame(rows)


def chart_diagnostics_from_strategy(strategy) -> ChartDiagnostics:
    """Build chart thresholds from a StrategyConfig-like object."""
    return ChartDiagnostics(
        atr_period=strategy.atr_period,
        strong_candle_body_ratio=strategy.strong_candle_body_ratio,
        strong_candle_atr_mult=strategy.strong_candle_atr_mult,
        spike_atr_mult=strategy.spike_atr_mult,
        chop_ema_atr_mult=strategy.chop_ema_atr_mult,
        entry_on_momentum_candle=strategy.entry_on_momentum_candle,
        entry_on_prev_break=strategy.entry_on_prev_break,
    )


def trades_with_signal_in_window(
    df: pd.DataFrame,
    trades: list[Trade],
    diag: ChartDiagnostics,
    t_min: datetime,
    t_max: datetime,
) -> list[Trade]:
    """Trades whose signal bar falls inside [t_min, t_max]."""
    out: list[Trade] = []
    for trade in trades:
        info = _analyze_signal_bar(df, trade, diag)
        if info is None:
            continue
        sig_time = df.iloc[info["sig_idx"]]["time"].to_pydatetime()
        if t_min <= sig_time <= t_max:
            out.append(trade)
    return out


def equity_figure(curve: list[tuple[datetime, float]], starting_balance: float) -> go.Figure:
    if not curve:
        fig = go.Figure()
        fig.update_layout(title="Equity curve", height=320)
        return fig
    df = pd.DataFrame(curve, columns=["time", "equity"])
    df["time_et"] = df["time"].dt.tz_convert(_ET)
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=df["time_et"],
            y=df["equity"],
            mode="lines",
            name="Equity",
            line=dict(color="#4f7cff", width=2),
            fill="tozeroy",
            fillcolor="rgba(79,124,255,0.08)",
        )
    )
    fig.add_hline(
        y=starting_balance,
        line_dash="dot",
        line_color="#8b93a7",
        annotation_text="Start",
    )
    fig.update_layout(
        title="Equity curve",
        height=320,
        margin=dict(l=20, r=20, t=40, b=20),
        xaxis_title="Time (ET)",
        yaxis_title="Account ($)",
        template="plotly_dark",
        paper_bgcolor="#0e1015",
        plot_bgcolor="#171a22",
    )
    return _apply_chart_interaction(fig)


def drawdown_figure(curve: list[tuple[datetime, float]]) -> go.Figure:
    if not curve:
        fig = go.Figure()
        fig.update_layout(title="Drawdown", height=260)
        return fig
    df = pd.DataFrame(curve, columns=["time", "equity"])
    df["time_et"] = df["time"].dt.tz_convert(_ET)
    df["peak"] = df["equity"].cummax()
    df["drawdown"] = df["equity"] - df["peak"]
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=df["time_et"],
            y=df["drawdown"],
            mode="lines",
            name="Drawdown",
            line=dict(color="#e5484d", width=2),
            fill="tozeroy",
            fillcolor="rgba(229,72,77,0.15)",
        )
    )
    fig.update_layout(
        title="Drawdown ($)",
        height=260,
        margin=dict(l=20, r=20, t=40, b=20),
        xaxis_title="Time (ET)",
        yaxis_title="Drawdown ($)",
        template="plotly_dark",
        paper_bgcolor="#0e1015",
        plot_bgcolor="#171a22",
    )
    return _apply_chart_interaction(fig)


def _price_offset(view: pd.DataFrame) -> float:
    if view.empty:
        return 5.0
    ranges = view["high"] - view["low"]
    return max(float(ranges.median()) * 0.35, 2.0)


def _trade_markers(
    trades: list[Trade],
    t_min: datetime,
    t_max: datetime,
    offset: float,
) -> tuple[list[dict], list[dict]]:
    """Build marker groups for TradingView-style entry/exit labels."""
    long_entries: list[dict] = []
    long_exits: list[dict] = []
    short_entries: list[dict] = []
    short_exits: list[dict] = []

    for trade in trades:
        if trade.exit_time < t_min or trade.entry_time > t_max:
            continue
        qty = trade.size
        sign_entry = f"+{qty}" if trade.side.value == "long" else f"-{qty}"
        sign_exit = f"-{qty}" if trade.side.value == "long" else f"+{qty}"
        entry_et = trade.entry_time.astimezone(_ET)
        exit_et = trade.exit_time.astimezone(_ET)
        pnl = trade.pnl_currency

        if trade.side.value == "long":
            long_entries.append(
                {
                    "x": entry_et,
                    "y": trade.entry_price - offset,
                    "text": f"{sign_entry} Long",
                    "hover": (
                        f"LONG fill (next-bar-open)<br>Price: {trade.entry_price:.2f}<br>"
                        f"Size: {qty}<br>{entry_et:%Y-%m-%d %H:%M} ET"
                    ),
                }
            )
            long_exits.append(
                {
                    "x": exit_et,
                    "y": trade.exit_price + offset,
                    "text": f"{sign_exit} Long X",
                    "hover": (
                        f"Close LONG<br>Price: {trade.exit_price:.2f}<br>"
                        f"PnL: ${pnl:,.2f}<br>{trade.exit_reason}<br>{exit_et:%Y-%m-%d %H:%M} ET"
                    ),
                }
            )
        else:
            short_entries.append(
                {
                    "x": entry_et,
                    "y": trade.entry_price + offset,
                    "text": f"{sign_entry} Short",
                    "hover": (
                        f"SHORT fill (next-bar-open)<br>Price: {trade.entry_price:.2f}<br>"
                        f"Size: {qty}<br>{entry_et:%Y-%m-%d %H:%M} ET"
                    ),
                }
            )
            short_exits.append(
                {
                    "x": exit_et,
                    "y": trade.exit_price - offset,
                    "text": f"{sign_exit} Short X",
                    "hover": (
                        f"Close SHORT<br>Price: {trade.exit_price:.2f}<br>"
                        f"PnL: ${pnl:,.2f}<br>{trade.exit_reason}<br>{exit_et:%Y-%m-%d %H:%M} ET"
                    ),
                }
            )

    return (
        [long_entries, long_exits, short_entries, short_exits],
        [],
    )


def _add_signal_body_highlights(
    fig: go.Figure,
    df: pd.DataFrame,
    trades: list[Trade],
    diag: ChartDiagnostics,
    t_min: datetime,
    t_max: datetime,
    timeframe_minutes: int,
) -> None:
    """Highlight signal-candle bodies so body vs range is visible."""
    pad = pd.Timedelta(minutes=max(1, timeframe_minutes) * 0.42)
    for trade in trades:
        info = _analyze_signal_bar(df, trade, diag)
        if info is None:
            continue
        row = df.iloc[info["sig_idx"]]
        sig_time = row["time"].to_pydatetime()
        if sig_time < t_min or sig_time > t_max:
            continue
        y0 = min(float(row["open"]), float(row["close"]))
        y1 = max(float(row["open"]), float(row["close"]))
        fill = "rgba(251,191,36,0.38)" if info["valid"] else "rgba(229,72,77,0.32)"
        fig.add_shape(
            type="rect",
            x0=row["time_et"] - pad,
            x1=row["time_et"] + pad,
            y0=y0,
            y1=y1,
            fillcolor=fill,
            line=dict(width=1, color=_CLR_SIGNAL if info["valid"] else "#e5484d"),
            layer="above",
        )


def _add_signal_markers(
    fig: go.Figure,
    df: pd.DataFrame,
    trades: list[Trade],
    diag: ChartDiagnostics,
    t_min: datetime,
    t_max: datetime,
    offset: float,
) -> None:
    """Mark the signal bar (strategy trigger candle) — one bar before the fill."""
    if df.empty or not trades:
        return
    signals: list[dict] = []
    for trade in trades:
        info = _analyze_signal_bar(df, trade, diag)
        if info is None:
            continue
        row = df.iloc[info["sig_idx"]]
        sig_time = row["time"].to_pydatetime()
        if sig_time < t_min or sig_time > t_max:
            continue
        y = float(row["high"]) + offset if trade.side is Side.LONG else float(row["low"]) - offset
        signals.append(
            {
                "x": row["time_et"],
                "y": y,
                "hover": _signal_hover_html(info, trade, diag),
            }
        )
    if not signals:
        return
    fig.add_trace(
        go.Scatter(
            x=[p["x"] for p in signals],
            y=[p["y"] for p in signals],
            mode="markers",
            name="Signal bar",
            marker=dict(
                symbol="diamond-open",
                size=11,
                color=_CLR_SIGNAL,
                line=dict(width=1.8, color=_CLR_SIGNAL),
            ),
            hovertext=[p["hover"] for p in signals],
            hoverinfo="text",
        )
    )


def _add_marker_trace(
    fig: go.Figure,
    points: list[dict],
    *,
    name: str,
    symbol: str,
    color: str,
    textposition: str,
    show_labels: bool = False,
) -> None:
    if not points:
        return
    mode = "markers+text" if show_labels else "markers"
    fig.add_trace(
        go.Scatter(
            x=[p["x"] for p in points],
            y=[p["y"] for p in points],
            mode=mode,
            name=name,
            text=[p["text"] for p in points] if show_labels else None,
            textposition=textposition if show_labels else None,
            textfont=dict(size=10, color=color, family="Inter, Segoe UI, sans-serif"),
            marker=dict(
                symbol=symbol,
                size=11,
                color=color,
                line=dict(width=1, color="#ffffff"),
            ),
            hovertext=[p["hover"] for p in points],
            hoverinfo="text",
        )
    )


def _add_position_bands(fig: go.Figure, trades: list[Trade], t_min: datetime, t_max: datetime) -> None:
    """Lightweight trade shading — shapes only (no extra line traces)."""
    for trade in trades:
        if trade.exit_time < t_min or trade.entry_time > t_max:
            continue
        y0 = min(trade.entry_price, trade.exit_price)
        y1 = max(trade.entry_price, trade.exit_price)
        fill = _CLR_POS_FILL if trade.side.value == "long" else _CLR_NEG_FILL
        fig.add_shape(
            type="rect",
            x0=trade.entry_time.astimezone(_ET),
            x1=trade.exit_time.astimezone(_ET),
            y0=y0,
            y1=y1,
            fillcolor=fill,
            line=dict(width=0),
            layer="below",
        )


def _viewport_xrange(viewport: pd.DataFrame, timeframe_minutes: int) -> list:
    """X range for the visible window with half-bar padding so candles aren't clipped."""
    if viewport.empty:
        return []
    pad = pd.Timedelta(minutes=max(1, timeframe_minutes) * 0.55)
    return [
        viewport["time_et"].iloc[0] - pad,
        viewport["time_et"].iloc[-1] + pad,
    ]


def _viewport_yrange(viewport: pd.DataFrame) -> list[float]:
    """Tight Y scale on the visible window so candles use vertical space."""
    y_lo = float(viewport["low"].min())
    y_hi = float(viewport["high"].max())
    span = y_hi - y_lo
    pad = max(span * 0.15, 3.0)
    return [y_lo - pad, y_hi + pad]


def render_bar_count(visible_bars: int) -> int:
    """How many candles to plot — enough to pan around, capped for performance."""
    n_vis = max(5, int(visible_bars))
    return min(CHART_MAX_RENDER_BARS, max(CHART_MIN_RENDER_BARS, n_vis * CHART_RENDER_MULT))


def _chart_render_slice(
    df: pd.DataFrame,
    *,
    visible_bars: int,
    bars_from_end: int,
) -> pd.DataFrame:
    """Bars sent to Plotly (a rolling buffer ending at bars_from_end)."""
    n = len(df)
    if n == 0:
        return df
    count = render_bar_count(visible_bars)
    end_idx = max(0, n - max(0, bars_from_end))
    start_idx = max(0, end_idx - count)
    return df.iloc[start_idx:end_idx]


def chart_render_window(
    df: pd.DataFrame,
    *,
    visible_bars: int,
    bars_from_end: int,
) -> pd.DataFrame:
    """Public wrapper for the rolling chart buffer slice."""
    return _chart_render_slice(df, visible_bars=visible_bars, bars_from_end=bars_from_end)


def _chart_viewport(render: pd.DataFrame, visible_bars: int) -> pd.DataFrame:
    """Initial x-axis window — rightmost visible_bars inside the render buffer."""
    if render.empty:
        return render
    n_vis = min(max(1, visible_bars), len(render))
    return render.iloc[-n_vis:]


def price_figure(
    df: pd.DataFrame,
    trades: list[Trade],
    *,
    visible_bars: int = 15,
    bars_from_end: int = 0,
    timeframe_minutes: int = 1,
    show_signal_diagnostics: bool = True,
    diag: ChartDiagnostics | None = None,
    show_trade_labels: bool = False,
) -> go.Figure:
    if df.empty:
        fig = go.Figure()
        fig.update_layout(title="Price + trades", height=640)
        return fig

    render = chart_render_window(df, visible_bars=visible_bars, bars_from_end=bars_from_end)
    viewport = _chart_viewport(render, visible_bars)
    n_visible = len(viewport)
    n_render = len(render)
    offset = _price_offset(viewport)
    t_min = render["time"].iloc[0].to_pydatetime()
    t_max = render["time"].iloc[-1].to_pydatetime()

    fig = make_subplots(rows=1, cols=1)

    fig.add_trace(
        go.Candlestick(
            x=render["time_et"],
            open=render["open"],
            high=render["high"],
            low=render["low"],
            close=render["close"],
            name="NQ",
            increasing_line_color="#26a69a",
            decreasing_line_color="#ef5350",
            increasing_fillcolor="#26a69a",
            decreasing_fillcolor="#ef5350",
        )
    )
    if "ema_fast" in render.columns:
        fig.add_trace(
            go.Scatter(
                x=render["time_et"],
                y=render["ema_fast"],
                mode="lines",
                name="EMA fast",
                line=dict(color="#ffb74d", width=1.6),
            )
        )
    if "ema_slow" in render.columns:
        fig.add_trace(
            go.Scatter(
                x=render["time_et"],
                y=render["ema_slow"],
                mode="lines",
                name="EMA slow",
                line=dict(color="#42a5f5", width=1.6),
            )
        )

    _add_position_bands(fig, trades, t_min, t_max)
    if show_signal_diagnostics and diag is not None:
        _add_signal_body_highlights(fig, df, trades, diag, t_min, t_max, timeframe_minutes)
        _add_signal_markers(fig, df, trades, diag, t_min, t_max, offset)
    groups, _ = _trade_markers(trades, t_min, t_max, offset)
    long_entries, long_exits, short_entries, short_exits = groups

    _add_marker_trace(
        fig, long_entries, name="Long fill", symbol="triangle-up",
        color=_CLR_LONG_ENTRY, textposition="bottom center", show_labels=show_trade_labels,
    )
    _add_marker_trace(
        fig, long_exits, name="Long exit", symbol="triangle-down",
        color=_CLR_LONG_EXIT, textposition="top center", show_labels=show_trade_labels,
    )
    _add_marker_trace(
        fig, short_entries, name="Short fill", symbol="triangle-down",
        color=_CLR_SHORT_ENTRY, textposition="top center", show_labels=show_trade_labels,
    )
    _add_marker_trace(
        fig, short_exits, name="Short exit", symbol="triangle-up",
        color=_CLR_SHORT_EXIT, textposition="bottom center", show_labels=show_trade_labels,
    )

    total_bars = len(df)
    fig.update_layout(
        title=dict(
            text=f"Price chart · {n_visible} on screen · {n_render} loaded · {total_bars:,} in run",
            x=0,
            xanchor="left",
            pad=dict(t=6, b=10),
        ),
        autosize=True,
        height=PRICE_CHART_HEIGHT,
        margin=dict(l=4, r=56, t=48, b=72),
        xaxis_title="Time (ET)",
        yaxis_title="Price",
        template="plotly_dark",
        paper_bgcolor="#0e1015",
        plot_bgcolor="#131722",
        xaxis_rangeslider_visible=False,
        uirevision=f"price-{bars_from_end}-{n_render}",
        legend=dict(
            orientation="h",
            yanchor="top",
            y=-0.24,
            xanchor="center",
            x=0.5,
            bgcolor="rgba(14,16,21,0.55)",
            font=dict(size=10),
        ),
        hovermode="closest",
        xaxis=dict(
            range=_viewport_xrange(viewport, timeframe_minutes),
            gridcolor="#2a2e39",
            showgrid=True,
            zeroline=False,
            tickfont=dict(size=12),
            fixedrange=False,
        ),
    )
    fig.update_yaxes(
        range=_viewport_yrange(viewport),
        gridcolor="#2a2e39",
        showgrid=True,
        zeroline=False,
        side="right",
        tickfont=dict(size=12),
        fixedrange=False,
    )
    # Fixed candle body width on the time axis (1m = 60_000 ms).
    xperiod_ms = max(1, timeframe_minutes) * 60 * 1000
    fig.update_traces(
        selector=dict(type="candlestick"),
        xperiod=xperiod_ms,
        xperiodalignment="middle",
        increasing_line_width=1.4,
        decreasing_line_width=1.4,
    )
    return _apply_chart_interaction(fig)


def monthly_pnl_figure(trades: list[Trade]) -> go.Figure:
    if not trades:
        fig = go.Figure()
        fig.update_layout(title="Monthly PnL", height=280)
        return fig
    rows = []
    for t in trades:
        rows.append(
            {
                "month": t.exit_time.astimezone(_ET).strftime("%Y-%m"),
                "pnl": t.pnl_currency,
            }
        )
    df = pd.DataFrame(rows).groupby("month", as_index=False)["pnl"].sum()
    colors = ["#27c281" if v >= 0 else "#e5484d" for v in df["pnl"]]
    fig = go.Figure(
        go.Bar(x=df["month"], y=df["pnl"], marker_color=colors, name="Monthly PnL")
    )
    fig.update_layout(
        title="Monthly net PnL",
        height=280,
        margin=dict(l=20, r=20, t=40, b=20),
        template="plotly_dark",
        paper_bgcolor="#0e1015",
        plot_bgcolor="#171a22",
    )
    return _apply_chart_interaction(fig)


def rejections_figure(rejections: dict[str, int]) -> go.Figure:
    if not rejections:
        fig = go.Figure()
        fig.update_layout(title="Signal rejections", height=280)
        return fig
    labels = list(rejections.keys())
    values = list(rejections.values())
    fig = go.Figure(go.Pie(labels=labels, values=values, hole=0.45))
    fig.update_layout(
        title="Signal rejections",
        height=280,
        template="plotly_dark",
        paper_bgcolor="#0e1015",
    )
    return _apply_chart_interaction(fig)
