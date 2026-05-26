"""Isolated strategy executor consuming exchange events outside the engine."""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from engine.core.order import Order
from engine.core.order_book import MatchResult
from engine.core.types import EventType, OrderId
from shared.events import BookUpdateEvent, DomainEvent, EventBus, TradeEvent
from strategy.base import BaseStrategy
from strategy.risk import StrategyRiskGuard


class OrderGateway(Protocol):
    """Narrow exchange access boundary; backed by OrderService in-process."""

    def submit(self, order: Order) -> MatchResult: ...

    def cancel(self, order_id: OrderId) -> Order | None: ...


@dataclass(slots=True)
class RuntimeMetrics:
    events_processed: int = 0
    orders_submitted: int = 0
    orders_rejected_by_risk: int = 0
    callback_failures: int = 0
    kill_switch_activations: int = 0


class StrategyRuntime:
    """
    Route ordered market events to independently controlled strategies.

    The runtime is deliberately downstream of EventBus. A malfunctioning
    strategy can be killed or fall behind without entering the synchronous
    matching call stack, a central isolation rule in exchange infrastructure.
    """

    SUBSCRIBER_NAME = "strategy_runtime"

    def __init__(self, event_bus: EventBus, gateway: OrderGateway) -> None:
        self._event_bus = event_bus
        self._gateway = gateway
        self._queue = event_bus.subscribe(
            EventType.BOOK_UPDATE,
            EventType.TRADE,
            subscriber_name=self.SUBSCRIBER_NAME,
        )
        self._strategies: dict[str, BaseStrategy] = {}
        self._risk: dict[str, StrategyRiskGuard] = {}
        self._owned_orders: dict[OrderId, tuple[BaseStrategy, Order]] = {}
        self._open_orders: dict[str, set[OrderId]] = {}
        self._stop_requested = False
        self.metrics = RuntimeMetrics()

    def add_strategy(
        self,
        strategy: BaseStrategy,
        risk_guard: StrategyRiskGuard | None = None,
    ) -> None:
        if strategy.strategy_id in self._strategies:
            raise ValueError(f"strategy already registered: {strategy.strategy_id}")
        self._strategies[strategy.strategy_id] = strategy
        self._risk[strategy.strategy_id] = risk_guard or StrategyRiskGuard()
        self._open_orders[strategy.strategy_id] = set()

    def start_strategy(self, strategy_id: str) -> None:
        self._strategies[strategy_id].start()

    def stop_strategy(self, strategy_id: str, reason: str | None = None) -> None:
        strategy = self._strategies[strategy_id]
        strategy.stop(reason)
        self._cancel_open_orders(strategy_id)

    def engage_kill_switch(self, strategy_id: str | None = None, reason: str = "operator kill switch") -> None:
        targets = [strategy_id] if strategy_id else list(self._strategies)
        for target in targets:
            self.stop_strategy(target, reason)
        self.metrics.kill_switch_activations += 1

    async def run(self) -> None:
        while not self._stop_requested:
            event = await self._queue.get()
            self.process_event(event)

    def request_stop(self) -> None:
        self._stop_requested = True

    def replay(self, events: Iterable[DomainEvent]) -> None:
        """Apply a recorded event sequence synchronously for deterministic testing."""
        for event in events:
            self.process_event(event)

    def process_event(self, event: DomainEvent) -> None:
        self.metrics.events_processed += 1
        if isinstance(event, BookUpdateEvent) and event.snapshot is not None:
            self._on_book_update(event)
        elif isinstance(event, TradeEvent) and event.trade is not None:
            self._on_trade(event)

    def strategy_metrics(self) -> dict[str, dict[str, float | int | str | None]]:
        return {
            strategy_id: {
                "position": strategy.state.position,
                "realised_pnl": strategy.state.realised_pnl,
                "unrealised_pnl": strategy.state.unrealised_pnl,
                "total_pnl": strategy.state.total_pnl,
                "orders_submitted": strategy.state.orders_submitted,
                "risk_rejections": strategy.state.risk_rejections,
                "killed_reason": strategy.state.killed_reason,
            }
            for strategy_id, strategy in self._strategies.items()
        }

    def _on_book_update(self, event: BookUpdateEvent) -> None:
        snapshot = event.snapshot
        assert snapshot is not None
        for strategy in self._strategies.values():
            if not strategy.is_running or strategy.symbol != event.symbol:
                continue
            if snapshot.mid_price is not None:
                strategy.mark_to_market(snapshot.mid_price)
            try:
                proposals = strategy.on_book_update(snapshot)
            except Exception as exc:
                self.metrics.callback_failures += 1
                self.stop_strategy(strategy.strategy_id, f"callback failure: {exc}")
                continue
            if proposals and strategy.replace_open_orders:
                self._cancel_open_orders(strategy.strategy_id)
            self._submit_proposals(strategy, proposals)

    def _on_trade(self, event: TradeEvent) -> None:
        trade = event.trade
        assert trade is not None
        # maker and taker can both be owned, including a deliberate self-cross.
        for order_id in (trade.maker_order_id, trade.taker_order_id):
            owned = self._owned_orders.get(order_id)
            if owned:
                strategy, order = owned
                strategy.on_fill(order, trade)
                if not order.is_active:
                    self._open_orders[strategy.strategy_id].discard(order_id)

        for strategy in self._strategies.values():
            if not strategy.is_running or strategy.symbol != event.symbol:
                continue
            try:
                self._submit_proposals(strategy, strategy.on_trade(trade))
            except Exception as exc:
                self.metrics.callback_failures += 1
                self.stop_strategy(strategy.strategy_id, f"callback failure: {exc}")

    def _submit_proposals(self, strategy: BaseStrategy, proposals: list[Order]) -> None:
        guard = self._risk[strategy.strategy_id]
        for order in proposals:
            if order.strategy_id != strategy.strategy_id or order.symbol != strategy.symbol:
                strategy.state.risk_rejections += 1
                self.metrics.orders_rejected_by_risk += 1
                continue
            decision = guard.evaluate(strategy, order)
            if not decision.accepted:
                strategy.state.risk_rejections += 1
                self.metrics.orders_rejected_by_risk += 1
                continue
            # Ownership is registered before the call; gateways may synchronously
            # generate fills whose asynchronous TradeEvent is consumed next.
            self._owned_orders[order.order_id] = (strategy, order)
            self._open_orders[strategy.strategy_id].add(order.order_id)
            self._gateway.submit(order)
            strategy.state.orders_submitted += 1
            self.metrics.orders_submitted += 1

    def _cancel_open_orders(self, strategy_id: str) -> None:
        for order_id in tuple(self._open_orders[strategy_id]):
            self._gateway.cancel(order_id)
            self._open_orders[strategy_id].discard(order_id)
