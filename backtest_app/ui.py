"""Professional results layout for the Streamlit dashboard."""
from __future__ import annotations

import streamlit as st

from backtest_app.datetime_utils import fmt_et, fmt_bar_end_et
from backtest_app.runner import WarmupBacktestResult
from backtest_app.data_loader import DataSlice

_CARD_CSS = """
<style>
    /* layout + background */
    [data-testid="stSidebar"] { display: none !important; }
    [data-testid="stSidebarCollapsedControl"] { display: none !important; }
    section.main > div { max-width: 100%; }
    .stApp { background: radial-gradient(circle at 20% 20%, #101522 0, #0c1019 45%, #0a0d14 100%); }
    .main, .block-container { padding-top: 0.6rem; }

    /* inputs + labels — lighter panels for readability */
    label, .stMarkdown p, .stCaption { color: #e6eaf2 !important; }
    label { font-weight: 600; }
    .stSelectbox div[data-baseweb="select"] > div,
    .stNumberInput input,
    .stTextInput input,
    .stDateInput input,
    .stTimeInput input,
    div[data-baseweb="input"] input,
    div[data-baseweb="select"] {
        background: #243049 !important;
        color: #f3f6fc !important;
        border: 1px solid #4a5f82 !important;
        border-radius: 10px !important;
    }
    div[data-baseweb="select"] > div {
        background: #243049 !important;
    }
    .stCheckbox > label {
        background: #243049;
        border: 1px solid #4a5f82;
        border-radius: 10px;
        padding: 8px 12px;
        display: inline-flex;
        align-items: center;
        gap: 8px;
        color: #f3f6fc !important;
    }
    .stCheckbox input[type="checkbox"] {
        width: 18px;
        height: 18px;
        accent-color: #4f7cff;
    }

    /* tabs — navigation strips (distinct from input widgets) */
    .config-nav .stTabs,
    .results-nav .stTabs {
        margin-top: 0.25rem;
        margin-bottom: 0.5rem;
    }
    .config-nav .stTabs [data-baseweb="tab-list"],
    .config-nav .stTabs [role="tablist"] {
        gap: 0 !important;
        background: linear-gradient(180deg, rgba(45, 212, 191, 0.14) 0%, rgba(45, 212, 191, 0.03) 100%) !important;
        border: 1px solid rgba(45, 212, 191, 0.35) !important;
        border-radius: 10px 10px 0 0 !important;
        padding: 6px 8px 0 8px !important;
    }
    .results-nav .stTabs [data-baseweb="tab-list"],
    .results-nav .stTabs [role="tablist"] {
        gap: 0 !important;
        background: linear-gradient(180deg, rgba(96, 165, 250, 0.16) 0%, rgba(96, 165, 250, 0.03) 100%) !important;
        border: 1px solid rgba(96, 165, 250, 0.35) !important;
        border-radius: 10px 10px 0 0 !important;
        padding: 6px 8px 0 8px !important;
    }
    .stTabs [data-baseweb="tab-list"] {
        gap: 0 !important;
        background: transparent !important;
        border-bottom: none !important;
        padding: 0 !important;
    }
    .stTabs [data-baseweb="tab"] {
        background: transparent !important;
        border: none !important;
        border-radius: 0 !important;
        box-shadow: none !important;
        color: #9aa3b5 !important;
        padding: 0.5rem 1.1rem !important;
        font-weight: 600 !important;
        border-bottom: 3px solid transparent !important;
        margin-bottom: 0 !important;
    }
    .stTabs [data-baseweb="tab"]:hover {
        color: #dce3f0 !important;
        background: rgba(255, 255, 255, 0.04) !important;
    }
    .stTabs [aria-selected="true"] {
        color: #ffffff !important;
        background: transparent !important;
        font-weight: 700 !important;
    }
    .config-nav .stTabs [aria-selected="true"] {
        color: #5eead4 !important;
        border-bottom-color: #2dd4bf !important;
    }
    .results-nav .stTabs [aria-selected="true"] {
        color: #bfdbfe !important;
        border-bottom-color: #60a5fa !important;
    }
    .results-nav [data-testid="stSelectbox"] > div > div {
        background: linear-gradient(180deg, rgba(96, 165, 250, 0.16) 0%, rgba(96, 165, 250, 0.03) 100%);
        border: 1px solid rgba(96, 165, 250, 0.35);
        border-radius: 10px;
    }
    @media (max-width: 768px) {
        .block-container { padding-left: 0.75rem; padding-right: 0.75rem; }
        .results-nav [data-testid="stHorizontalBlock"] {
            flex-wrap: wrap !important;
        }
    }
    .stTabs [data-baseweb="tab-panel"] {
        padding-top: 0.85rem !important;
        border: none !important;
        background: transparent !important;
    }

    /* legacy role=tab selectors (older streamlit) */
    .stTabs [role="tablist"] {
        gap: 0;
        border-bottom: none;
        background: transparent;
    }
    .stTabs [role="tablist"] button {
        background: transparent !important;
        color: #9aa3b5 !important;
        border: none !important;
        border-bottom: 3px solid transparent !important;
        border-radius: 0 !important;
        padding: 0.5rem 1.1rem;
        font-weight: 600;
        box-shadow: none !important;
    }
    .stTabs [role="tablist"] button[aria-selected="true"] {
        background: transparent !important;
        color: #ffffff !important;
        box-shadow: none !important;
    }
    .config-nav .stTabs [role="tablist"] button[aria-selected="true"] {
        color: #5eead4 !important;
        border-bottom-color: #2dd4bf !important;
    }
    .results-nav .stTabs [role="tablist"] button[aria-selected="true"] {
        color: #bfdbfe !important;
        border-bottom-color: #60a5fa !important;
    }

    .bt-align {
        padding: 0.7rem 0.95rem;
        border-radius: 10px;
        font-size: 0.88rem;
        line-height: 1.55;
        margin-bottom: 0.85rem;
        border: 1px solid #3d4f6f;
        background: #1a2233;
        color: #c8d0e0;
    }
    .bt-align b { color: #e8edf7; }

    /* price chart — full width, normal height (bar size via candle count, not panel size) */
    .price-chart-panel {
        width: 100%;
        margin: 0.25rem 0 0.5rem 0;
    }
    .price-chart-panel [data-testid="stPlotlyChart"] {
        width: 100% !important;
    }

    /* floating run button */
    .float-run-anchor {
        position: fixed;
        bottom: 1.35rem;
        right: 1.35rem;
        z-index: 9999;
        background: linear-gradient(135deg, #1a2744 0%, #0e1015 100%);
        border: 1px solid #4f7cff;
        border-radius: 14px;
        padding: 0.35rem 0.5rem 0.5rem;
        box-shadow: 0 8px 32px rgba(79,124,255,0.35);
    }
    .float-run-anchor button[kind="primary"] {
        min-width: 11rem;
        font-weight: 700;
        border-radius: 10px;
    }

    /* cards */
    .bt-card {
        background: #171a22;
        border: 1px solid #262b36;
        border-radius: 12px;
        padding: 1rem 1.1rem;
        margin-bottom: 0.75rem;
        box-shadow: 0 10px 28px rgba(0,0,0,0.35);
    }
    .bt-card h4 {
        margin: 0 0 0.65rem 0;
        font-size: 0.78rem;
        letter-spacing: 0.06em;
        text-transform: uppercase;
        color: #8b93a7;
        font-weight: 600;
    }
    .bt-kpi { font-size: 1.6rem; font-weight: 700; line-height: 1.2; color: #e6e8ee; }
    .bt-kpi.pos { color: #2bd588; }
    .bt-kpi.neg { color: #ff6b6b; }
    .bt-kpi.neu { color: #e6e8ee; }
    .bt-sub { font-size: 0.86rem; color: #9ea7ba; margin-top: 0.2rem; }
    .bt-row { display: grid; grid-template-columns: repeat(3, 1fr); gap: 0.85rem; }
    @media (max-width: 900px) { .bt-row { grid-template-columns: 1fr; } }
    .bt-meta { font-size: 0.9rem; color: #c4c9d4; line-height: 1.55; }
    .bt-status {
        padding: 0.65rem 0.9rem; border-radius: 10px; font-size: 0.92rem; margin-bottom: 0.9rem;
    }
    .bt-status.ok { background: #132819; border: 1px solid #1f5c3a; color: #7ddeb4; }
    .bt-status.warn { background: #2a2210; border: 1px solid #6b5418; color: #f0c060; }

    /* minis */
    .side-mini {
        background: #171a22; border: 1px solid #262b36; border-radius: 12px;
        padding: 1rem 1.1rem; text-align: center;
        box-shadow: 0 10px 28px rgba(0,0,0,0.3);
    }
    .side-mini .lbl { font-size: 0.78rem; color: #8b93a7; text-transform: uppercase; letter-spacing: 0.05em; }
    .side-mini .cnt { font-size: 1.7rem; font-weight: 700; color: #e6e8ee; margin: 0.35rem 0; }
    .side-mini .pnl { font-size: 1.0rem; font-weight: 600; }
</style>
"""


def inject_layout_css() -> None:
    st.markdown(_CARD_CSS, unsafe_allow_html=True)


def floating_run_button(label: str = "▶  Run backtest") -> bool:
    st.markdown('<div class="float-run-anchor">', unsafe_allow_html=True)
    run = st.button(label, type="primary", width="stretch", key="float_run_backtest")
    st.markdown("</div>", unsafe_allow_html=True)
    return run


def _pnl_class(value: float) -> str:
    return "pos" if value >= 0 else "neg"


def _pf_text(value: float) -> str:
    return "∞" if value == float("inf") else f"{value:.2f}"


def render_alignment_banner(cfg, topstep_halts: bool, trade_start: str, trade_end: str) -> None:
    flatten = cfg.market.extended_flatten_et if cfg.market.extended_hours else cfg.market.flatten_et
    mode = cfg.strategy.risk_mode
    st.markdown(
        f"""<div class="bt-align">
        <b>Backtest engine:</b> runs <code>TrendMomentumStrategy</code> from <code>engine/core/strategy.py</code>
        (same rules as client <code>strategy.py</code>).<br>
        <b>Chart markers:</b> ▲/▼ show <b>actual fills & exits</b> (next-bar-open + slippage) — not just signal candles.<br>
        <b>Session:</b> trade window {trade_start}–{trade_end} ET · flatten {flatten} ET ·
        TopStep daily halts {'ON' if topstep_halts else 'OFF'}.<br>
        <b>vs TradingView Pine:</b> Pine targets are sized from <b>signal-bar close</b>;
        <code>strategy.py</code> sizes targets from <b>fill price</b> — PnL can differ with same trade count.
        CSV data ends when bars end (may be before Jun 16).
        </div>""",
        unsafe_allow_html=True,
    )


def render_status(data_slice: DataSlice) -> None:
    if data_slice.clamped_to_data:
        st.markdown(
            f'<div class="bt-status warn">Start adjusted to data availability. '
            f'Requested from <b>{fmt_et(data_slice.requested_start)}</b>; '
            f'earliest test bar after <b>{data_slice.warmup_count}</b> warmup bars.</div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<div class="bt-status ok">Backtest complete — indicators warmed on startup candles.</div>',
            unsafe_allow_html=True,
        )


def render_summary_header(
    data_slice: DataSlice,
    *,
    instrument: str,
    timeframe: int,
    ema_fast: int,
    ema_slow: int,
    trade_start: str,
    trade_end: str,
) -> None:
    warmup_start = data_slice.warmup_bars[0].start if data_slice.warmup_bars else None
    end_through = fmt_bar_end_et(data_slice.end, timeframe)
    req_end = (
        fmt_et(data_slice.requested_end)
        if data_slice.requested_end and data_slice.requested_end > data_slice.end
        else None
    )
    end_note = (
        f'<br><span style="color:#9ea7ba">Requested end {req_end} · clamped to last bar in data</span>'
        if req_end
        else ""
    )
    st.markdown(
        f"""
        <div class="bt-row">
            <div class="bt-card">
                <h4>History window (New York)</h4>
                <div class="bt-meta">
                    <b>Start:</b> {fmt_et(data_slice.start)}<br>
                    <b>End (through):</b> {end_through}{end_note}<br>
                    <b>Test bars:</b> {data_slice.test_count:,}
                </div>
            </div>
            <div class="bt-card">
                <h4>Warmup (startup candles)</h4>
                <div class="bt-meta">
                    <b>Bars:</b> {data_slice.warmup_count}<br>
                    <b>From:</b> {fmt_et(warmup_start)}<br>
                    <b>Before test start</b>
                </div>
            </div>
            <div class="bt-card">
                <h4>Run configuration</h4>
                <div class="bt-meta">
                    <b>{instrument}</b> · {timeframe}m bars<br>
                    EMA {ema_fast}/{ema_slow}<br>
                    <b>Trade window:</b> {trade_start}–{trade_end} ET<br>
                    <code>strategy.py</code>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_performance_cards(result: WarmupBacktestResult, starting_balance: float) -> None:
    ret_pct = (
        (result.ending_balance - starting_balance) / starting_balance * 100
        if starting_balance
        else 0.0
    )
    wl = f"{result.wins}W / {result.losses}L"
    blocks = result.entry_blocks or {}
    block_total = sum(blocks.values())
    block_hint = ""
    if block_total:
        top = sorted(blocks.items(), key=lambda kv: kv[1], reverse=True)[:2]
        parts = [f"{k}: {v}" for k, v in top]
        block_hint = f'<div class="bt-sub">Entry blocks: {block_total:,} ({", ".join(parts)})</div>'
    st.markdown(
        f"""
        <div class="bt-row">
            <div class="bt-card">
                <h4>Performance</h4>
                <div class="bt-kpi {_pnl_class(result.net_pnl)}">${result.net_pnl:,.2f}</div>
                <div class="bt-sub">Net PnL · {ret_pct:+.2f}% on ${starting_balance:,.0f}</div>
                <div class="bt-sub">Ending balance ${result.ending_balance:,.2f}</div>
            </div>
            <div class="bt-card">
                <h4>Risk & quality</h4>
                <div class="bt-kpi neg">${result.max_drawdown:,.0f}</div>
                <div class="bt-sub">Max drawdown</div>
                <div class="bt-sub">Profit factor {_pf_text(result.profit_factor)} · {result.total_r:+.2f}R total</div>
            </div>
            <div class="bt-card">
                <h4>Activity</h4>
                <div class="bt-kpi neu">{result.trade_count}</div>
                <div class="bt-sub">Trades · win rate {result.win_rate * 100:.1f}%</div>
                <div class="bt-sub">{wl} · avg {result.avg_r:+.2f}R / trade</div>
                {block_hint}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_side_breakdown(result: WarmupBacktestResult) -> None:
    if not result.trades:
        return
    long_pnl = sum(t.pnl_currency for t in result.trades if t.side.value == "long")
    short_pnl = sum(t.pnl_currency for t in result.trades if t.side.value == "short")
    long_n = sum(1 for t in result.trades if t.side.value == "long")
    short_n = sum(1 for t in result.trades if t.side.value == "short")
    st.markdown("**Long vs short breakdown**")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown(
            f"""<div class="side-mini">
            <div class="lbl">Long trades</div>
            <div class="cnt">{long_n}</div>
            <div class="pnl {_pnl_class(long_pnl)}">${long_pnl:,.2f} net</div>
            </div>""",
            unsafe_allow_html=True,
        )
    with c2:
        st.markdown(
            f"""<div class="side-mini">
            <div class="lbl">Short trades</div>
            <div class="cnt">{short_n}</div>
            <div class="pnl {_pnl_class(short_pnl)}">${short_pnl:,.2f} net</div>
            </div>""",
            unsafe_allow_html=True,
        )
