"""Chart helpers for the Streamlit backtest dashboard."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from core.state import Trade

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


def add_indicators(df: pd.DataFrame, ema_fast: int, ema_slow: int) -> pd.DataFrame:
    out = df.copy()
    out["ema_fast"] = out["close"].ewm(span=ema_fast, adjust=False).mean()
    out["ema_slow"] = out["close"].ewm(span=ema_slow, adjust=False).mean()
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


def _add_signal_markers(fig: go.Figure, view: pd.DataFrame, trades: list[Trade]) -> None:
    """Mark the signal bar (strategy trigger candle) — one bar before the fill."""
    if view.empty or not trades:
        return
    times = view["time"].tolist()
    signals: list[dict] = []
    for trade in trades:
        try:
            idx = times.index(trade.entry_time)
        except ValueError:
            continue
        if idx <= 0:
            continue
        row = view.iloc[idx - 1]
        side = trade.side.value.upper()
        signals.append(
            {
                "x": row["time_et"],
                "y": row["close"],
                "hover": (
                    f"Signal bar ({side})<br>Strategy trigger · bar close {row['close']:.2f}<br>"
                    f"Fill on next bar open @ {trade.entry_price:.2f}"
                ),
            }
        )
    if not signals:
        return
    fig.add_trace(
        go.Scatter(
            x=[p["x"] for p in signals],
            y=[p["y"] for p in signals],
            mode="markers",
            name="Signal bar (strategy)",
            marker=dict(symbol="diamond-open", size=9, color=_CLR_SIGNAL, line=dict(width=1.5, color=_CLR_SIGNAL)),
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
) -> None:
    if not points:
        return
    fig.add_trace(
        go.Scatter(
            x=[p["x"] for p in points],
            y=[p["y"] for p in points],
            mode="markers+text",
            name=name,
            text=[p["text"] for p in points],
            textposition=textposition,
            textfont=dict(size=10, color=color, family="Inter, Segoe UI, sans-serif"),
            marker=dict(
                symbol=symbol,
                size=13,
                color=color,
                line=dict(width=1.2, color="#ffffff"),
            ),
            hovertext=[p["hover"] for p in points],
            hoverinfo="text",
        )
    )


def _add_position_bands(fig: go.Figure, trades: list[Trade], t_min: datetime, t_max: datetime) -> None:
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
        fig.add_trace(
            go.Scatter(
                x=[trade.entry_time.astimezone(_ET), trade.exit_time.astimezone(_ET)],
                y=[trade.entry_price, trade.exit_price],
                mode="lines",
                line=dict(
                    color=_CLR_LONG_ENTRY if trade.side.value == "long" else _CLR_SHORT_ENTRY,
                    width=1,
                    dash="dot",
                ),
                showlegend=False,
                hoverinfo="skip",
            )
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


def price_figure(
    df: pd.DataFrame,
    trades: list[Trade],
    *,
    visible_bars: int = 15,
    timeframe_minutes: int = 1,
    show_signal_bars: bool = False,
) -> go.Figure:
    if df.empty:
        fig = go.Figure()
        fig.update_layout(title="Price + trades", height=640)
        return fig

    full = df.copy()
    n_visible = min(max(1, visible_bars), len(full))
    # All bars are plotted; x-axis range sets the initial viewport only.
    viewport = full.iloc[-n_visible:]
    offset = _price_offset(viewport)
    t_min = full["time"].min()
    t_max = full["time"].max()

    fig = make_subplots(rows=1, cols=1)

    fig.add_trace(
        go.Candlestick(
            x=full["time_et"],
            open=full["open"],
            high=full["high"],
            low=full["low"],
            close=full["close"],
            name="NQ",
            increasing_line_color="#26a69a",
            decreasing_line_color="#ef5350",
            increasing_fillcolor="#26a69a",
            decreasing_fillcolor="#ef5350",
        )
    )
    if "ema_fast" in full.columns:
        fig.add_trace(
            go.Scatter(
                x=full["time_et"],
                y=full["ema_fast"],
                mode="lines",
                name="EMA fast",
                line=dict(color="#ffb74d", width=1.6),
            )
        )
    if "ema_slow" in full.columns:
        fig.add_trace(
            go.Scatter(
                x=full["time_et"],
                y=full["ema_slow"],
                mode="lines",
                name="EMA slow",
                line=dict(color="#42a5f5", width=1.6),
            )
        )

    _add_position_bands(fig, trades, t_min, t_max)
    if show_signal_bars:
        _add_signal_markers(fig, full, trades)
    groups, _ = _trade_markers(trades, t_min, t_max, offset)
    long_entries, long_exits, short_entries, short_exits = groups

    _add_marker_trace(
        fig, long_entries, name="Long fill", symbol="triangle-up",
        color=_CLR_LONG_ENTRY, textposition="bottom center",
    )
    _add_marker_trace(
        fig, long_exits, name="Long exit", symbol="triangle-down",
        color=_CLR_LONG_EXIT, textposition="top center",
    )
    _add_marker_trace(
        fig, short_entries, name="Short fill", symbol="triangle-down",
        color=_CLR_SHORT_ENTRY, textposition="top center",
    )
    _add_marker_trace(
        fig, short_exits, name="Short exit", symbol="triangle-up",
        color=_CLR_SHORT_EXIT, textposition="bottom center",
    )

    fig.update_layout(
        title=f"Price chart · {len(full):,} bars loaded · showing {n_visible}",
        autosize=True,
        height=PRICE_CHART_HEIGHT,
        margin=dict(l=4, r=56, t=40, b=16),
        xaxis_title="Time (ET)",
        yaxis_title="Price",
        template="plotly_dark",
        paper_bgcolor="#0e1015",
        plot_bgcolor="#131722",
        xaxis_rangeslider_visible=False,
        uirevision="price-chart",
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.01,
            xanchor="left",
            x=0,
            bgcolor="rgba(0,0,0,0)",
            font=dict(size=11),
        ),
        hovermode="x unified",
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
