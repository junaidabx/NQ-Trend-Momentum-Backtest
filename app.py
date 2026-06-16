"""NQ Trend-Momentum Strategy Backtester — Streamlit dashboard."""
from __future__ import annotations

import sys
from datetime import time, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent
ENGINE_SRC = ROOT / "engine"
if str(ENGINE_SRC) not in sys.path:
    sys.path.insert(0, str(ENGINE_SRC))

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
)
from backtest_app.config_ui import render_strategy_config  # noqa: E402
from backtest_app.data_loader import (  # noqa: E402
    data_bounds,
    default_window_end_start,
    discover_csv_paths,
    earliest_test_start,
    load_bars_from_csv,
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
def _cached_bars_from_upload(file_bytes: bytes, filename: str, target_tf: int):
    import io

    return load_bars_from_csv(io.BytesIO(file_bytes), target_timeframe_minutes=target_tf)


def _time_input(label: str, default: time, key: str) -> time:
    return st.time_input(label, value=default, step=timedelta(minutes=1), key=key)


def _render_data_source(bundled: list[Path]) -> tuple[list[Path], bytes | None, str, str]:
    c1, c2 = st.columns([1, 2])
    with c1:
        source_mode = st.radio(
            "Data source",
            ["Bundled CSV", "Upload CSV"],
            help="TradingView export: time,open,high,low,close (Unix seconds). Upload your own CSV for 3–6 month history.",
        )
    selected_paths: list[Path] = []
    upload_bytes: bytes | None = None
    upload_name = ""
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
            uploaded = st.file_uploader("Upload TradingView CSV", type=["csv"])
            if uploaded is None:
                st.info("Upload a CSV to continue.")
                st.stop()
            upload_bytes = uploaded.getvalue()
            upload_name = uploaded.name
            label = upload_name
    return selected_paths, upload_bytes, upload_name, label


def _render_history_window(bars, min_warmup: int) -> tuple[object, object, str]:
    first_utc, last_utc = data_bounds(bars)
    default_end, default_start = default_window_end_start(bars, months=3)
    def_start_et = utc_to_et(default_start)
    def_end_et = utc_to_et(default_end)
    earliest_utc = earliest_test_start(bars, min_warmup)

    st.markdown(
        f"**Available data (ET):** {fmt_et(first_utc)} → {fmt_et(last_utc)} · "
        f"**Earliest test start:** {fmt_et(earliest_utc)} ({min_warmup} warmup bars)"
    )
    st.caption(
        "CSV `time` column is **Unix UTC** (TradingView export). "
        f"Example: first bar stored as UTC **{first_utc.strftime('%Y-%m-%d %H:%M')}** → "
        f"**{fmt_et(first_utc)} ET**. Session windows and trade times use **America/New_York**."
    )

    use_defaults = st.checkbox(
        "Default history: 3 calendar months ending at latest bar",
        value=True,
        help="History window = which dates to replay. Separate from intraday trade window above.",
    )

    if use_defaults:
        st.caption(
            f"Start **{def_start_et.strftime('%Y-%m-%d %H:%M')} ET** · "
            f"End **{def_end_et.strftime('%Y-%m-%d %H:%M')} ET**"
        )
        return default_start, default_end, "Default (3 months to latest bar)"

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        sd = st.date_input("History start date", def_start_et.date(), key="hist_sd")
    with c2:
        st_ = _time_input("Start time (ET)", def_start_et.time().replace(second=0), "hist_st")
    with c3:
        ed = st.date_input("History end date", def_end_et.date(), key="hist_ed")
    with c4:
        et_ = _time_input("End time (ET)", def_end_et.time().replace(second=0), "hist_et")
    start_utc = et_to_utc(sd, st_)
    end_utc = et_to_utc(ed, et_)
    if start_utc < earliest_utc:
        st.warning(f"Warmup required — earliest start {fmt_et(earliest_utc)}")
    if start_utc >= end_utc:
        st.error("History start must be before end.")
    return start_utc, end_utc, f"Custom {fmt_et(start_utc)} → {fmt_et(end_utc)}"


def _history_file_tag(data_slice) -> str:
    s = utc_to_et(data_slice.start).strftime("%Y-%m-%d")
    e = utc_to_et(data_slice.end).strftime("%Y-%m-%d")
    return f"{s}_to_{e}"


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
        selected_paths, upload_bytes, upload_name, data_label = _render_data_source(bundled)

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
        if upload_bytes is not None:
            bars, src_tf, tgt_tf = _cached_bars_from_upload(upload_bytes, upload_name, target_tf)
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
    tab_overview, tab_chart, tab_trades, tab_rejects = st.tabs(
        ["Overview", "Price chart", "Trades", "Rejections"]
    )

    with tab_overview:
        left, right = st.columns([1.15, 0.85])
        with left:
            st.plotly_chart(
                equity_figure(result.equity_curve, starting_balance),
                width="stretch",
                config=PLOTLY_UI,
            )
            st.plotly_chart(drawdown_figure(result.equity_curve), width="stretch", config=PLOTLY_UI)
        with right:
            st.plotly_chart(monthly_pnl_figure(result.trades), width="stretch", config=PLOTLY_UI)
            render_side_breakdown(result)

    with tab_chart:
        chart_visible_bars = st.number_input(
            "Candles on screen",
            min_value=5,
            max_value=120,
            value=15,
            step=1,
            key="chart_visible_bars",
            help="Fewer = wider, taller candles. All history stays loaded — drag left to scroll.",
        )
        chart_bars = data_slice.warmup_bars + data_slice.test_bars
        df = bars_to_df(chart_bars)
        df = add_indicators(df, cfg.strategy.ema_fast, cfg.strategy.ema_slow)
        st.markdown('<div class="price-chart-panel">', unsafe_allow_html=True)
        st.plotly_chart(
            price_figure(
                df,
                trades_in_history_window(result, data_slice),
                visible_bars=int(chart_visible_bars),
                timeframe_minutes=target_tf,
                show_signal_bars=False,
            ),
            width="stretch",
            config=PRICE_CHART_UI,
        )
        st.markdown("</div>", unsafe_allow_html=True)
        st.caption(
            f"**{len(df):,}** bars loaded · **{int(chart_visible_bars)}** candles on screen · "
            f"{target_tf}m · {trade_start}–{trade_end} ET · "
            "Use **Candles on screen** to widen bars · drag left for older history"
        )

    with tab_trades:
        if result.trades:
            st.dataframe(trades_dataframe(result, data_slice), width="stretch", hide_index=True)
        else:
            st.warning("No trades — widen trade window or adjust parameters.")

    with tab_rejects:
        st.markdown("**Strategy signal rejections** (chop, spike, near-close — logged by `strategy.py`)")
        st.plotly_chart(rejections_figure(result.rejections), width="stretch", config=PLOTLY_UI)
        if result.rejections:
            st.dataframe(
                pd.DataFrame([{"Reason": k, "Count": v} for k, v in sorted(result.rejections.items())]),
                width="stretch",
                hide_index=True,
            )
        if result.entry_blocks:
            st.markdown("**Entry blocks** (risk / session — signal fired but entry not taken)")
            st.dataframe(
                pd.DataFrame([{"Reason": k, "Count": v} for k, v in sorted(result.entry_blocks.items(), key=lambda x: -x[1])]),
                width="stretch",
                hide_index=True,
            )
    st.markdown("</div>", unsafe_allow_html=True)


if __name__ == "__main__":
    main()
