"""NQ Trend-Momentum Strategy Backtester — Streamlit dashboard."""
from __future__ import annotations

import sys
from datetime import date, time, timedelta
from pathlib import Path

# Path setup must run before local package imports (Streamlit Cloud + reload safety).
ROOT = Path(__file__).resolve().parent
_root = str(ROOT)
_engine = str(ROOT / "engine")
if _root not in sys.path:
    sys.path.insert(0, _root)
if _engine not in sys.path:
    sys.path.insert(0, _engine)

import pandas as pd
import streamlit as st

from backtest_app.charts import (  # noqa: E402
    PLOTLY_UI,
    PRICE_CHART_UI,
    add_indicators,
    bars_to_df,
    drawdown_figure,
    equity_figure,
    monthly_pnl_figure,
    price_figure,
    rejections_figure,
    render_bar_count,
)
from backtest_app.config_ui import render_strategy_config  # noqa: E402
from backtest_app.data_loader import (  # noqa: E402
    data_bounds,
    default_window_end_start,
    discover_csv_paths,
    earliest_test_start,
    load_bars_from_paths,
    required_warmup_bars,
    slice_for_backtest,
)
from backtest_app.datetime_utils import et_to_utc, fmt_et, utc_to_et  # noqa: E402
from backtest_app.report import ReportContext, build_report_html, build_report_zip, trades_dataframe, trades_in_history_window  # noqa: E402
from backtest_app.runner import run_backtest_with_warmup  # noqa: E402
from backtest_app.ui import (  # noqa: E402
    floating_run_button,
    inject_layout_css,
    render_performance_cards,
    render_side_breakdown,
    render_status,
    render_summary_header,
    render_alignment_banner,
)
from infra.config import AppConfig  # noqa: E402

DATA_DIRS = [ROOT / "data", ROOT / "NQ Data"]
DEFAULT_STARTING_BALANCE = 50_000.0


@st.cache_data(show_spinner=False)
def _load_config() -> AppConfig:
    return AppConfig.load(ROOT / "config.yaml")


@st.cache_data(show_spinner="Loading bars…")
def _cached_bars_from_paths(paths_tuple: tuple[str, ...], target_tf: int):
    return load_bars_from_paths([Path(p) for p in paths_tuple], target_timeframe_minutes=target_tf)


@st.cache_data(show_spinner="Loading uploaded CSV…")
def _cached_bars_from_uploads(files_tuple: tuple[tuple[str, bytes], ...], target_tf: int):
    """Load uploads via temp files — reuses load_bars_from_paths (Streamlit-safe)."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        paths: list[Path] = []
        for i, (name, data) in enumerate(files_tuple):
            safe = Path(name).name or f"upload_{i}.csv"
            dest = Path(tmp) / safe
            if dest.exists():
                dest = Path(tmp) / f"{i}_{safe}"
            dest.write_bytes(data)
            paths.append(dest)
        return load_bars_from_paths(paths, target_timeframe_minutes=target_tf)


def _history_widget_key(data_key: str, field: str) -> str:
    safe = data_key.replace(":", "").replace("|", "_").replace("+", "")
    return f"hist_{field}_{safe}"


def _history_start_utc(sd: date) -> object:
    return et_to_utc(sd, time(0, 0))


def _history_end_utc(ed: date, *, last_utc, last_date: date) -> object:
    if ed >= last_date:
        return last_utc
    return et_to_utc(ed, time(23, 59))


def _render_history_window(bars, min_warmup: int) -> tuple[object, object, str]:
    first_utc, last_utc = data_bounds(bars)
    default_end, default_start = default_window_end_start(bars, months=3)
    def_start_et = utc_to_et(default_start)
    def_end_et = utc_to_et(default_end)
    earliest_utc = earliest_test_start(bars, min_warmup)

    first_date = utc_to_et(first_utc).date()
    last_date = utc_to_et(last_utc).date()
    earliest_date = utc_to_et(earliest_utc).date()
    def_start_date = max(first_date, min(def_start_et.date(), last_date))
    def_end_date = last_date

    data_key = f"{first_utc.isoformat()}|{last_utc.isoformat()}|{min_warmup}"

    st.markdown(
        f"**Available data (ET):** {fmt_et(first_utc)} → {fmt_et(last_utc)} · "
        f"**Earliest test start:** {fmt_et(earliest_utc)} ({min_warmup} warmup bars)"
    )
    st.caption(
        "CSV `time` column is **Unix UTC** (TradingView export). "
        f"Example: first bar stored as UTC **{first_utc.strftime('%Y-%m-%d %H:%M')}** → "
        f"**{fmt_et(first_utc)} ET**. Default = **3 calendar months** through the latest bar. "
        "End date is capped at the last bar in the file."
    )

    sd = st.date_input(
        "History start date (ET)",
        value=def_start_date,
        min_value=first_date,
        max_value=last_date,
        key=_history_widget_key(data_key, "sd"),
        help="Start of selected day (00:00 ET).",
    )
    ed = st.date_input(
        "History end date (ET)",
        value=def_end_date,
        min_value=first_date,
        max_value=last_date,
        key=_history_widget_key(data_key, "ed"),
        help="Through end of selected day, or exact last bar time on the final CSV date.",
    )

    if sd > ed:
        st.error("History start date must be on or before end date.")
    if sd < earliest_date:
        st.warning(f"Warmup required — earliest test start **{fmt_et(earliest_utc)}**")

    start_utc = _history_start_utc(sd)
    end_utc = _history_end_utc(ed, last_utc=last_utc, last_date=last_date)
    if end_utc > last_utc:
        end_utc = last_utc
    if start_utc >= end_utc:
        st.error("History start must be before end.")
    else:
        st.caption(f"Selected window: **{fmt_et(start_utc)}** → **{fmt_et(end_utc)}**")
    return start_utc, end_utc, f"{fmt_et(start_utc)} → {fmt_et(end_utc)}"


def _render_data_source(bundled: list[Path]) -> tuple[list[Path], list[tuple[str, bytes]] | None, str]:
    c1, c2 = st.columns([1, 2])
    with c1:
        source_mode = st.radio(
            "Data source",
            ["Bundled CSV", "Upload CSV"],
            help="TradingView export: time,open,high,low,close (Unix seconds). "
            "Upload one or more CSVs — merged and sorted by bar time automatically.",
        )
    selected_paths: list[Path] = []
    uploads: list[tuple[str, bytes]] | None = None
    label = ""

    with c2:
        if source_mode == "Bundled CSV":
            if not bundled:
                st.error("No CSV files in `data/` or `NQ Data/`.")
                st.stop()
            if st.checkbox("Concatenate all bundled files", value=True):
                selected_paths = bundled
                label = f"{len(bundled)} bundled CSV files (concatenated)"
            else:
                names = [p.name for p in bundled]
                pick = st.selectbox("Select file", names, index=len(names) - 1)
                selected_paths = [next(p for p in bundled if p.name == pick)]
                label = pick
        else:
            uploaded = st.file_uploader(
                "Upload TradingView CSV",
                type=["csv"],
                accept_multiple_files=True,
                help="Select multiple files in any order — bars are merged and sorted by time.",
            )
            if not uploaded:
                st.info("Upload one or more CSV files to continue.")
                st.stop()
            files = uploaded if isinstance(uploaded, list) else [uploaded]
            uploads = [(f.name, f.getvalue()) for f in files]
            if len(uploads) == 1:
                label = uploads[0][0]
            else:
                label = f"{len(uploads)} uploaded CSV files (merged by time)"
            st.caption("Files: " + ", ".join(name for name, _ in uploads))
    return selected_paths, uploads, label


def _history_file_tag(data_slice) -> str:
    s = utc_to_et(data_slice.start).strftime("%Y-%m-%d")
    e = utc_to_et(data_slice.end).strftime("%Y-%m-%d")
    return f"{s}_to_{e}"


def _rerun_chart_panel() -> None:
    try:
        st.rerun(scope="fragment")
    except TypeError:
        st.rerun()


_fragment = getattr(st, "fragment", lambda f: f)


@_fragment
def _render_price_chart(
    df: pd.DataFrame,
    trades,
    *,
    target_tf: int,
    trade_start: time,
    trade_end: time,
) -> None:
    """Isolated chart panel — reruns only when its controls change."""
    if "chart_bars_from_end" not in st.session_state:
        st.session_state.chart_bars_from_end = 0

    c1, c2, c3 = st.columns([1.1, 1.2, 1.1])
    with c1:
        chart_visible_bars = st.number_input(
            "Candles on screen",
            min_value=5,
            max_value=120,
            value=15,
            step=1,
            key="chart_visible_bars",
            help="Fewer = wider candles. Drag chart left/right to pan within loaded window.",
        )
    with c2:
        max_back = max(0, len(df) - int(chart_visible_bars))
        bars_from_end = int(st.session_state.chart_bars_from_end)
        bars_from_end = max(0, min(bars_from_end, max_back))
        st.session_state.chart_bars_from_end = bars_from_end
        nav_step = max(int(chart_visible_bars), render_bar_count(int(chart_visible_bars)) // 3)
        if st.button("◀ Older history", help="Load earlier candles beyond current pan window"):
            st.session_state.chart_bars_from_end = min(max_back, bars_from_end + nav_step)
            _rerun_chart_panel()
        if st.button("Newer ▶", help="Move toward latest bars"):
            st.session_state.chart_bars_from_end = max(0, bars_from_end - nav_step)
            _rerun_chart_panel()
    with c3:
        st.caption(
            f"Loaded **{render_bar_count(int(chart_visible_bars))}** bars · "
            f"offset **{bars_from_end}** from end"
        )

    fig = price_figure(
        df,
        trades,
        visible_bars=int(chart_visible_bars),
        bars_from_end=bars_from_end,
        timeframe_minutes=target_tf,
        show_signal_bars=False,
        show_trade_labels=False,
    )
    st.markdown('<div class="price-chart-panel">', unsafe_allow_html=True)
    st.plotly_chart(
        fig,
        use_container_width=True,
        config=PRICE_CHART_UI,
        key="price_chart",
    )
    st.markdown("</div>", unsafe_allow_html=True)

    st.caption(
        f"**{len(df):,}** bars in run · **{int(chart_visible_bars)}** on screen · "
        f"**drag to pan** within loaded window · **◀ Older** for earlier history · "
        f"{target_tf}m · {trade_start}–{trade_end} ET"
    )


def main() -> None:
    st.set_page_config(
        page_title="NQ Strategy Backtest",
        page_icon="📈",
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    inject_layout_css()

    base_config = _load_config()
    bundled = discover_csv_paths(*DATA_DIRS)

    st.title("NQ Trend-Momentum Backtest")
    st.caption("Configure below · all times **New York (ET)** · run with the floating button ↘")

    # ── Configuration (top) ─────────────────────────────────────────
    with st.container(border=True):
        st.subheader("Data")
        selected_paths, uploads, data_label = _render_data_source(bundled)

    with st.container(border=True):
        st.subheader("Strategy & session")
        cfg, topstep_halts = render_strategy_config(base_config)
        if cfg.strategy.ema_slow <= cfg.strategy.ema_fast:
            st.stop()

    target_tf = cfg.market.timeframe_minutes
    min_warmup = required_warmup_bars(
        cfg.strategy.ema_slow,
        cfg.strategy.atr_period,
        cfg.strategy.stop_swing_lookback,
    )
    m = cfg.market
    trade_start = m.extended_window.start_et if m.extended_hours else m.trading_window.start_et
    trade_end = m.extended_window.end_et if m.extended_hours else m.trading_window.end_et

    try:
        if uploads is not None:
            bars, src_tf, tgt_tf = _cached_bars_from_uploads(tuple(uploads), target_tf)
        else:
            bars, src_tf, tgt_tf = _cached_bars_from_paths(
                tuple(str(p) for p in selected_paths), target_tf
            )
    except Exception as exc:
        st.error(f"Failed to load data: {exc}")
        st.stop()

    with st.container(border=True):
        st.subheader("History window")
        start_utc, end_utc, history_mode = _render_history_window(bars, min_warmup)
        starting_balance = st.number_input(
            "Starting balance ($)",
            min_value=1_000.0,
            value=DEFAULT_STARTING_BALANCE,
            step=1_000.0,
            help="Account size for equity curve and risk calculations.",
        )

    run = floating_run_button()

    if run:
        try:
            data_slice = slice_for_backtest(
                bars,
                min_warmup=min_warmup,
                start=start_utc,
                end=end_utc,
                source_timeframe_minutes=src_tf,
                target_timeframe_minutes=tgt_tf,
            )
            with st.spinner("Running backtest…"):
                result = run_backtest_with_warmup(
                    cfg,
                    data_slice.warmup_bars,
                    data_slice.test_bars,
                    starting_balance=starting_balance,
                )
            st.session_state["bt_result"] = result
            st.session_state["bt_data_slice"] = data_slice
            st.session_state["bt_report_ctx"] = ReportContext(
                cfg=cfg,
                data_slice=data_slice,
                result=result,
                starting_balance=starting_balance,
                trade_start_et=trade_start,
                trade_end_et=trade_end,
                data_source=data_label,
                history_mode=history_mode,
            )
            st.session_state["bt_cfg"] = cfg
            st.session_state["bt_topstep_halts"] = topstep_halts
            st.session_state["bt_starting_balance"] = starting_balance
            st.session_state["bt_trade_start"] = trade_start
            st.session_state["bt_trade_end"] = trade_end
            st.session_state["bt_target_tf"] = target_tf
            chart_bars = data_slice.warmup_bars + data_slice.test_bars
            st.session_state["bt_chart_df"] = add_indicators(
                bars_to_df(chart_bars),
                cfg.strategy.ema_fast,
                cfg.strategy.ema_slow,
            )
            st.session_state["bt_chart_trades"] = trades_in_history_window(result, data_slice)
            st.session_state.chart_bars_from_end = 0
        except Exception as exc:
            st.error(f"Backtest failed: {exc}")
            st.stop()

    if "bt_result" not in st.session_state:
        st.info("Set parameters above, then click **▶ Run backtest** (bottom-right).")
        st.stop()

    result = st.session_state["bt_result"]
    data_slice = st.session_state["bt_data_slice"]
    report_ctx = st.session_state["bt_report_ctx"]
    cfg = st.session_state["bt_cfg"]
    topstep_halts = st.session_state["bt_topstep_halts"]
    starting_balance = st.session_state["bt_starting_balance"]
    trade_start = st.session_state["bt_trade_start"]
    trade_end = st.session_state["bt_trade_end"]
    target_tf = st.session_state["bt_target_tf"]

    # ── Results (below config) ────────────────────────────────────────
    st.divider()
    st.subheader("Results")

    dl1, dl2, dl3 = st.columns(3)
    hist_tag = _history_file_tag(data_slice)
    with dl1:
        st.download_button(
            "Download full report (ZIP)",
            build_report_zip(report_ctx),
            file_name=f"nq_backtest_{hist_tag}.zip",
            mime="application/zip",
            help="HTML report + trades.csv + run_config.json with all parameters.",
            width="stretch",
        )
    with dl2:
        st.download_button(
            "Download report (HTML)",
            build_report_html(report_ctx).encode(),
            file_name=f"nq_backtest_{hist_tag}.html",
            mime="text/html",
            width="stretch",
        )
    with dl3:
        st.download_button(
            "Download trades (CSV)",
            trades_dataframe(result, data_slice).to_csv(index=False).encode(),
            file_name=f"nq_trades_{hist_tag}.csv",
            mime="text/csv",
            width="stretch",
        )

    render_status(data_slice)
    render_alignment_banner(cfg, topstep_halts, trade_start, trade_end)
    render_summary_header(
        data_slice,
        instrument=cfg.market.instrument,
        timeframe=target_tf,
        ema_fast=cfg.strategy.ema_fast,
        ema_slow=cfg.strategy.ema_slow,
        trade_start=trade_start,
        trade_end=trade_end,
    )
    render_performance_cards(result, starting_balance)

    st.markdown('<div class="results-nav">', unsafe_allow_html=True)
    results_view = st.selectbox(
        "Results view",
        ["Overview", "Price chart", "Trades", "Rejections"],
        key="results_view",
    )

    if results_view == "Overview":
        left, right = st.columns([1.15, 0.85])
        with left:
            st.plotly_chart(
                equity_figure(result.equity_curve, starting_balance),
                use_container_width=True,
                config=PLOTLY_UI,
            )
            st.plotly_chart(drawdown_figure(result.equity_curve), use_container_width=True, config=PLOTLY_UI)
        with right:
            st.plotly_chart(monthly_pnl_figure(result.trades), use_container_width=True, config=PLOTLY_UI)
            render_side_breakdown(result)

    elif results_view == "Price chart":
        if "bt_chart_df" not in st.session_state:
            chart_bars = data_slice.warmup_bars + data_slice.test_bars
            st.session_state["bt_chart_df"] = add_indicators(
                bars_to_df(chart_bars),
                cfg.strategy.ema_fast,
                cfg.strategy.ema_slow,
            )
            st.session_state["bt_chart_trades"] = trades_in_history_window(result, data_slice)
        _render_price_chart(
            st.session_state["bt_chart_df"],
            st.session_state["bt_chart_trades"],
            target_tf=target_tf,
            trade_start=trade_start,
            trade_end=trade_end,
        )

    elif results_view == "Trades":
        if result.trades:
            st.dataframe(
                trades_dataframe(result, data_slice),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.warning("No trades — widen trade window or adjust parameters.")

    elif results_view == "Rejections":
        st.markdown("**Strategy signal rejections** (chop, spike, near-close — logged by `strategy.py`)")
        st.plotly_chart(rejections_figure(result.rejections), use_container_width=True, config=PLOTLY_UI)
        if result.rejections:
            st.dataframe(
                pd.DataFrame([{"Reason": k, "Count": v} for k, v in sorted(result.rejections.items())]),
                use_container_width=True,
                hide_index=True,
            )
        if result.entry_blocks:
            st.markdown("**Entry blocks** (risk / session — signal fired but entry not taken)")
            st.dataframe(
                pd.DataFrame([{"Reason": k, "Count": v} for k, v in sorted(result.entry_blocks.items(), key=lambda x: -x[1])]),
                use_container_width=True,
                hide_index=True,
            )
    st.markdown("</div>", unsafe_allow_html=True)


if __name__ == "__main__":
    main()
