"""Replay historical bars through the same strategy/broker/risk stack the engine
uses live, so backtest results match paper mode over the same history.

The per-bar loop mirrors ``Engine._on_closed_bar``. ``run_backtest`` is pure
(bars in, result out); ``fetch_and_backtest`` is the async wrapper the GUI calls.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from broker.paper_broker import PaperBroker
from infra.config import AppConfig, Secrets

from .bars import Bar
from .risk import RiskManager, SessionClock
from .state import Position, Trade
from .strategy import TrendMomentumStrategy

log = logging.getLogger("backtest")

_DEFAULT_BALANCE = 50_000.0


@dataclass
class BacktestResult:
    """Replay outcome: trades plus summary stats for the GUI."""
    instrument: str
    timeframe_minutes: int
    bars: int
    start: Optional[datetime]
    end: Optional[datetime]
    starting_balance: float
    ending_balance: float
    trades: list[Trade] = field(default_factory=list)
    rejections: dict[str, int] = field(default_factory=dict)

    # filled by run_backtest
    net_pnl: float = 0.0
    wins: int = 0
    losses: int = 0
    total_r: float = 0.0
    max_drawdown: float = 0.0
    gross_win: float = 0.0
    gross_loss: float = 0.0

    @property
    def trade_count(self) -> int:
        return len(self.trades)

    @property
    def win_rate(self) -> float:
        return self.wins / self.trade_count if self.trade_count else 0.0

    @property
    def avg_r(self) -> float:
        return self.total_r / self.trade_count if self.trade_count else 0.0

    @property
    def profit_factor(self) -> float:
        if self.gross_loss == 0:
            return float("inf") if self.gross_win > 0 else 0.0
        return self.gross_win / self.gross_loss

    def headline(self) -> str:
        return (f"{self.trade_count} trades · net ${self.net_pnl:,.2f} · "
                f"win {self.win_rate * 100:.0f}% · {self.total_r:+.1f}R · "
                f"maxDD ${self.max_drawdown:,.0f}")


class _Tally:
    """Running bookkeeping the broker callbacks mutate as trades close."""

    def __init__(self) -> None:
        self.cumulative_realized = 0.0   # whole backtest; drives equity
        self.realized_today = 0.0        # resets each session; drives daily-loss
        self.trades_today = 0
        self.consecutive_losses = 0
        self.wins = 0
        self.losses = 0
        self.total_r = 0.0
        self.gross_win = 0.0
        self.gross_loss = 0.0
        self.trades: list[Trade] = []

    def on_trade_closed(self, trade: Trade) -> None:
        self.trades.append(trade)
        self.cumulative_realized += trade.pnl_currency
        self.realized_today += trade.pnl_currency
        self.trades_today += 1
        self.total_r += trade.r_multiple
        if trade.pnl_currency > 0:
            self.wins += 1
            self.consecutive_losses = 0
            self.gross_win += trade.pnl_currency
        else:
            self.losses += 1
            self.consecutive_losses += 1
            self.gross_loss += -trade.pnl_currency

    def reset_daily(self) -> None:
        self.realized_today = 0.0
        self.trades_today = 0
        self.consecutive_losses = 0


def run_backtest(config: AppConfig, bars: list[Bar],
                 starting_balance: float = _DEFAULT_BALANCE) -> BacktestResult:
    """Replay ``bars`` through the strategy/broker/risk stack synchronously."""
    spec = config.market.spec
    strategy = TrendMomentumStrategy(config.strategy, spec.tick_size, config.market)
    broker = PaperBroker(config.execution, spec, config.exits)
    risk = RiskManager(config.risk, starting_balance)
    clock = SessionClock(config.market)
    tally = _Tally()
    broker.on_trade_closed = tally.on_trade_closed

    session_key: Optional[str] = None
    halted_for_day = False
    peak_equity = starting_balance
    max_dd = 0.0
    size = config.risk.contracts_per_trade

    def equity_now() -> float:
        pos: Optional[Position] = broker.position
        open_pnl = pos.open_pnl if pos else 0.0
        return starting_balance + tally.cumulative_realized + open_pnl

    for bar in bars:
        # session rollover: reset per-day tallies and the strategy session
        key = clock.session_key(bar.start)
        if session_key is not None and key != session_key:
            strategy.reset_session()
            tally.reset_daily()
            halted_for_day = False
        session_key = key

        # broker advances fills/exits against this bar (may close a trade)
        broker.on_bar(bar)

        equity = equity_now()
        risk.update_equity(equity)
        peak_equity = max(peak_equity, equity)
        max_dd = max(max_dd, peak_equity - equity)

        breach = risk.breach(realized_pnl_today=tally.realized_today,
                             consecutive_losses=tally.consecutive_losses)
        if breach and not halted_for_day:
            if broker.position is not None or broker.has_pending_entry:
                broker.flatten(breach, bar.close, bar.end)
            halted_for_day = True
            continue  # mirror the engine: skip indicator update on a breach bar

        if clock.should_flatten(bar.start):
            if broker.position is not None or broker.has_pending_entry:
                broker.flatten("EOD", bar.close, bar.end)
            halted_for_day = True

        # update indicators (always) and maybe enter
        in_pos = broker.position is not None
        signal = strategy.on_bar(bar, in_position=in_pos)
        if signal is None or in_pos or halted_for_day:
            continue
        if broker.has_pending_entry or not clock.can_open(bar.start):
            continue

        risk_ticks = abs(signal.ref_price - signal.stop_price) / spec.tick_size
        projected_risk = risk_ticks * spec.tick_value * size
        verdict = risk.can_enter(
            equity=equity, realized_pnl_today=tally.realized_today,
            trades_today=tally.trades_today,
            consecutive_losses=tally.consecutive_losses,
            projected_risk=projected_risk)
        if not verdict.allowed:
            if verdict.halt:
                halted_for_day = True
            continue
        broker.submit_entry(signal, size, bar)

    result = BacktestResult(
        instrument=spec.symbol,
        timeframe_minutes=config.market.timeframe_minutes,
        bars=len(bars),
        start=bars[0].start if bars else None,
        end=bars[-1].start if bars else None,
        starting_balance=starting_balance,
        ending_balance=starting_balance + tally.cumulative_realized,
        trades=tally.trades,
        rejections=dict(strategy.rejections),
        net_pnl=tally.cumulative_realized,
        wins=tally.wins,
        losses=tally.losses,
        total_r=tally.total_r,
        max_drawdown=max_dd,
        gross_win=tally.gross_win,
        gross_loss=tally.gross_loss,
    )
    log.info("backtest done: %s", result.headline())
    return result


async def fetch_and_backtest(config: AppConfig, secrets: Secrets, days: int,
                             starting_balance: Optional[float] = None) -> BacktestResult:
    """Authenticate, pull ``days`` of history, and replay it. GUI entry point."""
    # lazy import so the pure replay path has no httpx/network dependency
    from broker.projectx_client import ProjectXClient

    client = ProjectXClient(secrets)
    try:
        await client.authenticate()
        balance = starting_balance
        if balance is None:
            accounts = await client.search_accounts()
            balance = (float(accounts[0].get("balance", _DEFAULT_BALANCE))
                       if accounts else _DEFAULT_BALANCE)
        contract_id = await client.resolve_contract_id(config.market.instrument)
        bars = await client.retrieve_history(
            contract_id, config.market.timeframe_minutes, days)
    finally:
        await client.aclose()

    if not bars:
        raise RuntimeError(
            "no historical bars returned for the requested range — "
            "try fewer days or check the instrument/contract")
    return run_backtest(config, bars, balance)
