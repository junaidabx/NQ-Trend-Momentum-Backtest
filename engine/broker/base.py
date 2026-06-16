"""Broker interface shared by the paper and live brokers.

The engine talks only to this interface, pushing each closed bar in via
``on_bar`` and submitting entries built from a Signal. Results come back through
the ``on_entry_fill`` and ``on_trade_closed`` callbacks set by the engine.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Callable, Optional

from core.bars import Bar
from core.state import Position, Signal, Trade

OnEntryFill = Callable[[Position], None]
OnTradeClosed = Callable[[Trade], None]


class Broker(ABC):
    mode: str = "paper"

    def __init__(self) -> None:
        self.on_entry_fill: Optional[OnEntryFill] = None
        self.on_trade_closed: Optional[OnTradeClosed] = None

    @property
    @abstractmethod
    def position(self) -> Optional[Position]:
        ...

    @property
    @abstractmethod
    def has_pending_entry(self) -> bool:
        ...

    @abstractmethod
    def submit_entry(self, signal: Signal, size: int, bar: Bar) -> str:
        """Register an entry. Returns the trade id."""

    @abstractmethod
    def cancel_pending(self) -> None:
        """Drop an entry that has not filled yet."""

    @abstractmethod
    def on_bar(self, bar: Bar) -> None:
        """Advance fills/exits against a freshly closed bar."""

    @abstractmethod
    def flatten(self, reason: str, price: float, when: datetime) -> None:
        """Close any open position immediately at ``price``."""
