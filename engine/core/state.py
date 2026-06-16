"""Thread-safe shared state and the GUI/engine command channel.

The engine writes to BotState under a lock and the GUI reads immutable
snapshots; the GUI sends Command objects through a queue the engine drains each
loop. Neither thread touches the other's objects directly.
"""
from __future__ import annotations

import queue
import threading
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class EngineState(str, Enum):
    STOPPED = "STOPPED"
    IDLE = "IDLE"
    SEARCHING = "SEARCHING"
    PULLBACK_DETECTED = "PULLBACK_DETECTED"
    ARMED = "ARMED"
    IN_TRADE = "IN_TRADE"
    HALTED = "HALTED"


class Side(str, Enum):
    LONG = "long"
    SHORT = "short"

    @property
    def sign(self) -> int:
        return 1 if self is Side.LONG else -1


@dataclass(frozen=True)
class Signal:
    side: Side
    stop_price: float
    tp_ratio: float          # target = entry +/- tp_ratio * |entry - stop|
    signal_time: datetime
    reason: str
    ref_price: float         # provisional entry estimate (fill is next-bar-open)
    # Fixed-tick risk plan. When both are set the bracket is derived from the
    # fill +/- a fixed tick distance instead of structure stop + tp_ratio.
    fixed_stop_ticks: Optional[int] = None
    fixed_tp_ticks: Optional[int] = None

    def bracket(self, entry: float, tick_size: float) -> tuple[float, float]:
        """Return (stop_price, target_price) for an actual fill at ``entry``.

        Fixed mode: stop/target sit a fixed tick distance either side of the
        fill. Dynamic mode: keep the structure stop and place the target at
        tp_ratio x the entry-to-stop risk.
        """
        if self.fixed_stop_ticks is not None and self.fixed_tp_ticks is not None:
            stop = entry - self.side.sign * self.fixed_stop_ticks * tick_size
            target = entry + self.side.sign * self.fixed_tp_ticks * tick_size
            return stop, target
        risk = abs(entry - self.stop_price)
        target = entry + self.side.sign * self.tp_ratio * risk
        return self.stop_price, target


@dataclass
class Position:
    side: Side
    size: int
    entry_price: float
    stop_price: float
    target_price: float
    entry_time: datetime
    open_pnl: float = 0.0
    trade_id: str = ""
    # two-stage stop manager bookkeeping
    peak_price: float = 0.0       # best price seen (peak for long / trough for short)
    breakeven_set: bool = False   # stage 1: stop moved to break-even
    trailing_active: bool = False  # stage 2: stop trailing to lock profit


@dataclass
class Trade:
    """A closed trade kept in memory for the GUI."""
    trade_id: str
    mode: str
    symbol: str
    side: Side
    size: int
    entry_time: datetime
    entry_price: float
    stop_price: float
    target_price: float
    exit_time: datetime
    exit_price: float
    exit_reason: str
    pnl_currency: float
    r_multiple: float


# Commands (GUI -> engine)
class CommandType(str, Enum):
    START = "start"
    STOP = "stop"
    UPDATE_CONFIG = "update_config"
    SET_MODE = "set_mode"
    KILL = "kill"
    FLATTEN = "flatten"


@dataclass
class Command:
    type: CommandType
    payload: object = None


# Shared state
@dataclass
class StateSnapshot:
    """Immutable view handed to the GUI each refresh."""
    engine_state: EngineState
    mode: str
    running: bool
    api_connected: bool
    feed_connected: bool
    account_id: Optional[str]
    balance: float
    realized_pnl_today: float
    drawdown_headroom: Optional[float]
    position: Optional[Position]
    trades_today: int
    wins: int
    losses: int
    total_r: float
    consecutive_losses: int
    halted_reason: str
    last_bar_time: Optional[datetime]
    recent_trades: tuple[Trade, ...]


class BotState:
    """Mutable engine state behind a lock, plus a command queue."""

    def __init__(self, mode: str = "paper") -> None:
        self._lock = threading.RLock()
        self.commands: "queue.Queue[Command]" = queue.Queue()

        self.engine_state = EngineState.STOPPED
        self.mode = mode
        self.running = False
        self.api_connected = False
        self.feed_connected = False

        self.account_id: Optional[str] = None
        self.balance = 0.0
        self.realized_pnl_today = 0.0
        self.drawdown_headroom: Optional[float] = None

        self.position: Optional[Position] = None
        self.trades_today = 0
        self.wins = 0
        self.losses = 0
        self.total_r = 0.0
        self.consecutive_losses = 0
        self.halted_reason = ""
        self.last_bar_time: Optional[datetime] = None
        self._recent_trades: list[Trade] = []

    # mutation helpers (engine side)
    def update(self, **fields) -> None:
        with self._lock:
            for k, v in fields.items():
                setattr(self, k, v)

    def set_state(self, state: EngineState, reason: str = "") -> None:
        with self._lock:
            self.engine_state = state
            if state is EngineState.HALTED and reason:
                self.halted_reason = reason

    def record_trade(self, trade: Trade) -> None:
        with self._lock:
            self._recent_trades.append(trade)
            self.realized_pnl_today += trade.pnl_currency
            self.trades_today += 1
            self.total_r += trade.r_multiple
            if trade.pnl_currency > 0:
                self.wins += 1
                self.consecutive_losses = 0
            else:
                self.losses += 1
                self.consecutive_losses += 1

    def reset_daily(self) -> None:
        with self._lock:
            self.trades_today = 0
            self.wins = 0
            self.losses = 0
            self.total_r = 0.0
            self.realized_pnl_today = 0.0
            self.consecutive_losses = 0
            self._recent_trades.clear()

    # read side (GUI)
    def snapshot(self) -> StateSnapshot:
        with self._lock:
            pos = replace(self.position) if self.position else None
            return StateSnapshot(
                engine_state=self.engine_state,
                mode=self.mode,
                running=self.running,
                api_connected=self.api_connected,
                feed_connected=self.feed_connected,
                account_id=self.account_id,
                balance=self.balance,
                realized_pnl_today=self.realized_pnl_today,
                drawdown_headroom=self.drawdown_headroom,
                position=pos,
                trades_today=self.trades_today,
                wins=self.wins,
                losses=self.losses,
                total_r=self.total_r,
                consecutive_losses=self.consecutive_losses,
                halted_reason=self.halted_reason,
                last_bar_time=self.last_bar_time,
                recent_trades=tuple(self._recent_trades[-100:]),
            )

    # command channel (GUI -> engine)
    def send(self, cmd: Command) -> None:
        self.commands.put(cmd)

    def drain_commands(self) -> list[Command]:
        out: list[Command] = []
        while True:
            try:
                out.append(self.commands.get_nowait())
            except queue.Empty:
                break
        return out


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
