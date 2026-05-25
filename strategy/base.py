"""
Abstract strategy interface.

The Strategy pattern separates trading logic from execution infrastructure.
A strategy receives market events and emits orders. It never touches sockets,
databases, or the matching engine directly — those are infrastructure concerns.

This mirrors the quant/dev split at HFT firms:
  - Quants own Strategy subclasses (pure math, market logic)
  - Devs own the execution infrastructure (OrderService, risk, connectivity)

A quant can write a strategy knowing only:
  - on_book_update(snapshot) → [Order, ...]
  - on_trade(trade)          → [Order, ...]
  - on_fill(order, trade)    → None

And trust that the infrastructure handles everything else.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from engine.core.order import Order, Trade
from engine.core.order_book import BookSnapshot
from engine.core.types import Symbol


@dataclass
class StrategyState:
    """Runtime state tracked per strategy. Mutable during execution."""
    position: int = 0          # net shares (positive = long, negative = short)
    realised_pnl: float = 0.0  # locked-in P&L from closed positions
    unrealised_pnl: float = 0.0
    total_fills: int = 0
    total_notional: float = 0.0


class Strategy(ABC):
    """
    Base class for all strategies.

    Subclass this, implement on_book_update and on_trade,
    return a list of Order objects to submit. Empty list = do nothing.

    The executor (not yet implemented — Milestone 5) calls these methods
    as events arrive and submits the returned orders through OrderService.
    """

    def __init__(
        self,
        strategy_id: str,
        symbol: Symbol,
        capital: float = 100_000.0,
        max_position: int = 1_000,
    ) -> None:
        self.strategy_id = strategy_id
        self.symbol = symbol
        self.capital = capital
        self.max_position = max_position
        self._state = StrategyState()
        self._is_running = False

    # ── Abstract interface ────────────────────────────────────────────────────

    @abstractmethod
    def on_book_update(self, snapshot: BookSnapshot) -> list[Order]:
        """
        Called on every order book update for `self.symbol`.
        Return orders to submit (may be empty).
        """
        ...

    @abstractmethod
    def on_trade(self, trade: Trade) -> list[Order]:
        """
        Called on every public trade print for `self.symbol`.
        Return orders to submit (may be empty).
        """
        ...

    # ── Lifecycle hooks (optional override) ───────────────────────────────────

    def on_fill(self, order: Order, trade: Trade) -> None:
        """
        Called when one of *this strategy's* orders is filled.
        Update P&L and position tracking here.
        """
        qty = trade.quantity
        notional = trade.price * qty

        if order.is_buy:
            self._state.position += qty
            self._state.unrealised_pnl -= notional
        else:
            self._state.position -= qty
            self._state.unrealised_pnl += notional

        self._state.total_fills += 1
        self._state.total_notional += notional

    def on_start(self) -> None:
        """Called when the strategy is started. Override for initialisation."""
        pass

    def on_stop(self) -> None:
        """Called before the strategy is stopped. Override for cleanup."""
        pass

    # ── Control ───────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._is_running = True
        self.on_start()

    def stop(self) -> None:
        self._is_running = False
        self.on_stop()

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def is_running(self) -> bool:
        return self._is_running

    @property
    def state(self) -> StrategyState:
        return self._state

    @property
    def position(self) -> int:
        return self._state.position

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}("
            f"id={self.strategy_id}, "
            f"pos={self._state.position}, "
            f"pnl={self._state.realised_pnl:.2f})"
        )
