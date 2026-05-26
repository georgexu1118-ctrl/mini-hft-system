"""Strategy contract and position/PnL accounting kept outside matching."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from engine.core.order import Order, Trade
from engine.core.order_book import BookSnapshot
from engine.core.types import Symbol


@dataclass(slots=True)
class StrategyState:
    """Mutable strategy ledger; filled inventory is valued at the latest mark."""

    position: int = 0
    average_entry_price: float = 0.0
    realised_pnl: float = 0.0
    unrealised_pnl: float = 0.0
    mark_price: float | None = None
    total_fills: int = 0
    total_notional: float = 0.0
    orders_submitted: int = 0
    risk_rejections: int = 0
    killed_reason: str | None = None

    @property
    def total_pnl(self) -> float:
        return self.realised_pnl + self.unrealised_pnl


class BaseStrategy(ABC):
    """
    Event-driven strategy contract.

    Strategies return order intents only. The runtime applies limits and uses
    an order gateway, so no strategy can mutate engine books or bypass risk.
    That separation also permits the same callback sequence to be replayed.
    """

    # Quote-style strategies request cancel/replace semantics on a new quote set.
    replace_open_orders: bool = False

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

    @abstractmethod
    def on_book_update(self, snapshot: BookSnapshot) -> list[Order]:
        """Return orders proposed in response to level-two market data."""
        ...

    @abstractmethod
    def on_trade(self, trade: Trade) -> list[Order]:
        """Return orders proposed in response to an execution print."""
        ...

    def on_fill(self, order: Order, trade: Trade) -> None:
        """Update average-cost PnL for one fill in an owned order."""
        signed_quantity = trade.quantity if order.is_buy else -trade.quantity
        prior_position = self._state.position
        prior_average = self._state.average_entry_price

        if prior_position == 0 or prior_position * signed_quantity > 0:
            new_position = prior_position + signed_quantity
            prior_value = abs(prior_position) * prior_average
            fill_value = abs(signed_quantity) * trade.price
            self._state.average_entry_price = (prior_value + fill_value) / abs(new_position)
        else:
            closing_quantity = min(abs(prior_position), abs(signed_quantity))
            direction = 1 if prior_position > 0 else -1
            self._state.realised_pnl += (
                (trade.price - prior_average) * closing_quantity * direction
            )
            new_position = prior_position + signed_quantity
            if new_position == 0:
                self._state.average_entry_price = 0.0
            elif prior_position * new_position < 0:
                self._state.average_entry_price = trade.price

        self._state.position = prior_position + signed_quantity
        self._state.total_fills += 1
        self._state.total_notional += trade.notional
        self.mark_to_market(trade.price)

    def mark_to_market(self, price: float) -> None:
        self._state.mark_price = price
        self._state.unrealised_pnl = (
            (price - self._state.average_entry_price) * self._state.position
        )

    def on_start(self) -> None:
        """Optional initialization hook."""

    def on_stop(self) -> None:
        """Optional shutdown hook."""

    def start(self) -> None:
        self._state.killed_reason = None
        self._is_running = True
        self.on_start()

    def stop(self, reason: str | None = None) -> None:
        self._is_running = False
        self._state.killed_reason = reason
        self.on_stop()

    @property
    def is_running(self) -> bool:
        return self._is_running

    @property
    def state(self) -> StrategyState:
        return self._state

    @property
    def position(self) -> int:
        return self._state.position


# Preserve the public name used by existing example strategies.
Strategy = BaseStrategy
