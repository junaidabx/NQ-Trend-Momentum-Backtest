"""Backtest runner with indicator warmup (startup candles)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from broker.paper_broker import PaperBroker
from core.backtest import BacktestResult
from core.bars import Bar
from core.risk import RiskManager, SessionClock
from core.state import Position, Trade
from core.strategy import TrendMomentumStrategy
from infra.config import AppConfig

_DEFAULT_BALANCE = 50_000.0


@dataclass
class WarmupBacktestResult(BacktestResult):
    """Backtest result plus warmup metadata for the UI."""

    warmup_bars: int = 0
    test_bars: int = 0
    backtest_start: Optional[datetime] = None
    backtest_end: Optional[datetime] = None
    equity_curve: list[tuple[datetime, float]] = field(default_factory=list)
    entry_blocks: dict[str, int] = field(default_factory=dict)


class _Tally:
    def __init__(self) -> None:
        self.cumulative_realized = 0.0
        self.realized_today = 0.0
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


def run_backtest_with_warmup(
    config: AppConfig,
    warmup_bars: list[Bar],
    test_bars: list[Bar],
    starting_balance: float = _DEFAULT_BALANCE,
) -> WarmupBacktestResult:
    """Prime indicators on warmup bars, then replay the test window."""
    spec = config.market.spec
    strategy = TrendMomentumStrategy(config.strategy, spec.tick_size, config.market)
    if warmup_bars:
        strategy.prime(warmup_bars)

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
    equity_curve: list[tuple[datetime, float]] = []
    entry_blocks: dict[str, int] = {}

    def _block(reason: str) -> None:
        entry_blocks[reason] = entry_blocks.get(reason, 0) + 1

    def equity_now() -> float:
        pos: Optional[Position] = broker.position
        open_pnl = pos.open_pnl if pos else 0.0
        return starting_balance + tally.cumulative_realized + open_pnl

    for bar in test_bars:
        key = clock.session_key(bar.start)
        if session_key is not None and key != session_key:
            strategy.reset_session()
            tally.reset_daily()
            halted_for_day = False
        session_key = key

        broker.on_bar(bar)

        equity = equity_now()
        risk.update_equity(equity)
        peak_equity = max(peak_equity, equity)
        max_dd = max(max_dd, peak_equity - equity)
        equity_curve.append((bar.start, equity))

        breach = risk.breach(
            realized_pnl_today=tally.realized_today,
            consecutive_losses=tally.consecutive_losses,
        )
        if breach and not halted_for_day:
            if broker.position is not None or broker.has_pending_entry:
                broker.flatten(breach, bar.close, bar.end)
            halted_for_day = True
            _block(breach)
            continue

        if clock.should_flatten(bar.start):
            if broker.position is not None or broker.has_pending_entry:
                broker.flatten("EOD", bar.close, bar.end)
            halted_for_day = True

        in_pos = broker.position is not None
        signal = strategy.on_bar(bar, in_position=in_pos)
        if signal is None or in_pos or halted_for_day:
            continue
        if broker.has_pending_entry:
            continue
        if not clock.can_open(bar.start):
            _block("outside trade window")
            continue

        risk_ticks = abs(signal.ref_price - signal.stop_price) / spec.tick_size
        projected_risk = risk_ticks * spec.tick_value * size
        verdict = risk.can_enter(
            equity=equity,
            realized_pnl_today=tally.realized_today,
            trades_today=tally.trades_today,
            consecutive_losses=tally.consecutive_losses,
            projected_risk=projected_risk,
        )
        if not verdict.allowed:
            _block(verdict.reason)
            if verdict.halt:
                halted_for_day = True
            continue
        broker.submit_entry(signal, size, bar)

    # Close any open position at the end of the replay window.
    if test_bars and broker.position is not None:
        last = test_bars[-1]
        broker.flatten("END_OF_BACKTEST", last.close, last.end or last.start)

    return WarmupBacktestResult(
        instrument=spec.symbol,
        timeframe_minutes=config.market.timeframe_minutes,
        bars=len(test_bars),
        start=test_bars[0].start if test_bars else None,
        end=test_bars[-1].start if test_bars else None,
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
        warmup_bars=len(warmup_bars),
        test_bars=len(test_bars),
        backtest_start=test_bars[0].start if test_bars else None,
        backtest_end=test_bars[-1].start if test_bars else None,
        equity_curve=equity_curve,
        entry_blocks=entry_blocks,
    )
