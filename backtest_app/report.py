"""HTML / CSV backtest report generation."""
from __future__ import annotations

import io
import json
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape
from typing import Any

import pandas as pd

from backtest_app.data_loader import DataSlice
from backtest_app.datetime_utils import fmt_et, fmt_bar_end_et
from backtest_app.runner import WarmupBacktestResult
from infra.config import AppConfig


@dataclass
class ReportContext:
    cfg: AppConfig
    data_slice: DataSlice
    result: WarmupBacktestResult
    starting_balance: float
    trade_start_et: str
    trade_end_et: str
    data_source: str
    history_mode: str
    generated_at: datetime = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.generated_at is None:
            self.generated_at = datetime.now(timezone.utc)


def trades_in_history_window(
    result: WarmupBacktestResult,
    data_slice: DataSlice,
) -> list:
    """Trades whose entry falls inside the replay window (ET-safe, UTC compare)."""
    window_end = data_slice.end
    if data_slice.target_timeframe_minutes:
        from backtest_app.datetime_utils import bar_close_et
        window_end = bar_close_et(data_slice.end, data_slice.target_timeframe_minutes)
    return [
        t for t in result.trades
        if data_slice.start <= t.entry_time <= window_end
    ]


def trades_dataframe(
    result: WarmupBacktestResult,
    data_slice: DataSlice | None = None,
) -> pd.DataFrame:
    from zoneinfo import ZoneInfo

    et = ZoneInfo("America/New_York")
    trades = trades_in_history_window(result, data_slice) if data_slice else result.trades
    rows = []
    for t in trades:
        rows.append(
            {
                "trade_id": t.trade_id,
                "side": t.side.value.upper(),
                "entry_time_et": t.entry_time.astimezone(et).strftime("%Y-%m-%d %H:%M:%S"),
                "exit_time_et": t.exit_time.astimezone(et).strftime("%Y-%m-%d %H:%M:%S"),
                "entry_price": round(t.entry_price, 2),
                "exit_price": round(t.exit_price, 2),
                "stop_price": round(t.stop_price, 2),
                "target_price": round(t.target_price, 2),
                "exit_reason": t.exit_reason,
                "pnl_usd": round(t.pnl_currency, 2),
                "r_multiple": round(t.r_multiple, 2),
            }
        )
    return pd.DataFrame(rows)


def _config_sections(ctx: ReportContext) -> dict[str, dict[str, Any]]:
    c = ctx.cfg
    s = c.strategy
    m = c.market
    return {
        "Run info": {
            "Generated (UTC)": ctx.generated_at.strftime("%Y-%m-%d %H:%M:%S UTC"),
            "Data source": ctx.data_source,
            "History selection": ctx.history_mode,
            "Starting balance": f"${ctx.starting_balance:,.2f}",
        },
        "History window (ET)": {
            "Test start": fmt_et(ctx.data_slice.start),
            "Test end (through)": fmt_bar_end_et(ctx.data_slice.end, ctx.cfg.market.timeframe_minutes),
            "Warmup bars": ctx.data_slice.warmup_count,
            "Test bars": ctx.data_slice.test_count,
            "Requested start": fmt_et(ctx.data_slice.requested_start),
            "Requested end": fmt_et(ctx.data_slice.requested_end) if ctx.data_slice.requested_end else "—",
        },
        "Intraday trade window (ET)": {
            "Trade start": ctx.trade_start_et,
            "Trade end": ctx.trade_end_et,
            "Extended session clock": m.extended_hours,
            "Flatten (ET)": m.extended_flatten_et if m.extended_hours else m.flatten_et,
        },
        "Market": {
            "Instrument": m.instrument,
            "Bar timeframe (min)": m.timeframe_minutes,
            "VWAP anchor": m.vwap_anchor,
        },
        "Strategy": s.model_dump(),
        "Risk": c.risk.model_dump(),
        "Execution": c.execution.model_dump(),
        "Exits": c.exits.model_dump(),
    }


def _summary_rows(ctx: ReportContext) -> list[tuple[str, str]]:
    r = ctx.result
    pf = r.profit_factor
    pf_s = "∞" if pf == float("inf") else f"{pf:.2f}"
    ret = (r.ending_balance - ctx.starting_balance) / ctx.starting_balance * 100 if ctx.starting_balance else 0
    return [
        ("Net PnL", f"${r.net_pnl:,.2f}"),
        ("Return", f"{ret:+.2f}%"),
        ("Ending balance", f"${r.ending_balance:,.2f}"),
        ("Trades", str(r.trade_count)),
        ("Win rate", f"{r.win_rate * 100:.1f}%"),
        ("W / L", f"{r.wins} / {r.losses}"),
        ("Total R", f"{r.total_r:+.2f}R"),
        ("Max drawdown", f"${r.max_drawdown:,.0f}"),
        ("Profit factor", pf_s),
    ]


def build_report_html(ctx: ReportContext) -> str:
    r = ctx.result
    trades_df = trades_dataframe(r, ctx.data_slice)
    rejections = r.rejections or {}

    def table_from_dict(d: dict[str, Any]) -> str:
        rows = "".join(
            f"<tr><th>{escape(str(k))}</th><td>{escape(str(v))}</td></tr>" for k, v in d.items()
        )
        return f"<table class='kv'>{rows}</table>"

    config_html = "".join(
        f"<section><h2>{escape(title)}</h2>{table_from_dict(data)}</section>"
        for title, data in _config_sections(ctx).items()
    )

    summary_html = "".join(
        f"<div class='kpi'><div class='label'>{escape(k)}</div><div class='value'>{escape(v)}</div></div>"
        for k, v in _summary_rows(ctx)
    )

    if trades_df.empty:
        trades_html = "<p class='muted'>No trades in this run.</p>"
    else:
        trades_html = trades_df.to_html(index=False, classes="trades", border=0, escape=True)

    rej_html = (
        "<ul>" + "".join(f"<li><b>{escape(k)}</b>: {v}</li>" for k, v in sorted(rejections.items())) + "</ul>"
        if rejections
        else "<p class='muted'>None recorded.</p>"
    )

    long_n = sum(1 for t in r.trades if t.side.value == "long")
    short_n = sum(1 for t in r.trades if t.side.value == "short")
    long_pnl = sum(t.pnl_currency for t in r.trades if t.side.value == "long")
    short_pnl = sum(t.pnl_currency for t in r.trades if t.side.value == "short")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>NQ Backtest Report — {escape(fmt_et(ctx.data_slice.end))}</title>
<style>
  :root {{ --bg:#0e1015; --card:#171a22; --border:#262b36; --text:#e6e8ee; --muted:#8b93a7;
           --green:#27c281; --red:#e5484d; --blue:#4f7cff; }}
  body {{ font-family: Segoe UI, system-ui, sans-serif; background: var(--bg); color: var(--text);
          margin: 0; padding: 2rem; line-height: 1.5; }}
  h1 {{ font-size: 1.75rem; margin: 0 0 0.25rem; }}
  .subtitle {{ color: var(--muted); margin-bottom: 2rem; }}
  section {{ background: var(--card); border: 1px solid var(--border); border-radius: 12px;
              padding: 1.25rem 1.5rem; margin-bottom: 1.25rem; }}
  h2 {{ font-size: 0.85rem; text-transform: uppercase; letter-spacing: 0.06em; color: var(--muted);
        margin: 0 0 1rem; }}
  .kpis {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 0.75rem; }}
  .kpi {{ background: #0b0d12; border: 1px solid var(--border); border-radius: 8px; padding: 0.85rem; }}
  .kpi .label {{ font-size: 0.75rem; color: var(--muted); text-transform: uppercase; }}
  .kpi .value {{ font-size: 1.25rem; font-weight: 700; margin-top: 0.25rem; }}
  table.kv {{ width: 100%; border-collapse: collapse; font-size: 0.9rem; }}
  table.kv th {{ text-align: left; color: var(--muted); font-weight: 500; width: 40%; padding: 0.35rem 0.5rem; }}
  table.kv td {{ padding: 0.35rem 0.5rem; }}
  table.trades {{ width: 100%; border-collapse: collapse; font-size: 0.82rem; }}
  table.trades th {{ background: #0b0d12; color: var(--muted); text-align: left; padding: 0.5rem; }}
  table.trades td {{ border-top: 1px solid var(--border); padding: 0.45rem 0.5rem; }}
  .side-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; }}
  .side-box {{ background: #0b0d12; border-radius: 8px; padding: 1rem; border: 1px solid var(--border); }}
  .pos {{ color: var(--green); }} .neg {{ color: var(--red); }}
  .muted {{ color: var(--muted); }}
</style>
</head>
<body>
<h1>NQ Strategy Backtest Report</h1>
<p class="subtitle">TrendMomentumStrategy · {escape(ctx.cfg.market.instrument)} · {ctx.cfg.market.timeframe_minutes}m · New York (ET)</p>

<section><h2>Performance summary</h2><div class="kpis">{summary_html}</div></section>

<section><h2>Long vs short</h2>
<div class="side-grid">
  <div class="side-box"><div class="muted">Long trades</div><div style="font-size:1.5rem;font-weight:700">{long_n}</div>
    <div class="{'pos' if long_pnl >= 0 else 'neg'}">${long_pnl:,.2f} net</div></div>
  <div class="side-box"><div class="muted">Short trades</div><div style="font-size:1.5rem;font-weight:700">{short_n}</div>
    <div class="{'pos' if short_pnl >= 0 else 'neg'}">${short_pnl:,.2f} net</div></div>
</div></section>

{config_html}

<section><h2>Trades</h2>{trades_html}</section>
<section><h2>Signal rejections</h2>{rej_html}</section>
</body>
</html>"""


def build_report_zip(ctx: ReportContext) -> bytes:
    html = build_report_html(ctx)
    trades_csv = trades_dataframe(ctx.result, ctx.data_slice).to_csv(index=False).encode()
    config_json = json.dumps(
        {
            "generated_at": ctx.generated_at.isoformat(),
            "config": ctx.cfg.model_dump(mode="json"),
            "history_window": {
                "start_et": fmt_et(ctx.data_slice.start),
                "end_et": fmt_et(ctx.data_slice.end),
                "warmup_bars": ctx.data_slice.warmup_count,
                "test_bars": ctx.data_slice.test_count,
            },
            "trade_window_et": {"start": ctx.trade_start_et, "end": ctx.trade_end_et},
            "data_source": ctx.data_source,
            "summary": dict(_summary_rows(ctx)),
        },
        indent=2,
    ).encode()

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("backtest_report.html", html)
        zf.writestr("trades.csv", trades_csv)
        zf.writestr("run_config.json", config_json)
    return buf.getvalue()
