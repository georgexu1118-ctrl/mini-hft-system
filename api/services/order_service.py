"""
OrderService: bridges the matching engine and async infrastructure.

The engine is synchronous (pure computation). The API is async (I/O driven).
This service layer is the translation point:

  1. Convert API request DTOs → engine Order objects
  2. Call engine.submit_order() synchronously (fast enough for simulation)
  3. Engine invokes our callback synchronously while matching
  4. Callback uses put_nowait() — fire-and-forget, never blocks the engine
  5. EventBus fans out to WebSocket broadcaster and DB writer

The callback MUST be synchronous and MUST NOT await anything.
This is the core discipline that keeps the hot path clean.
"""
from __future__ import annotations
import time
from typing import Optional

from engine.core.matching_engine import MatchingEngine, EngineEvent
from engine.core.order import Order
from engine.core.order_book import MatchResult
from engine.core.types import EventType, OrderId

from shared.events import (
    EventBus,
    OrderAckedEvent,
    OrderFilledEvent,
    OrderPartiallyFilledEvent,
    OrderCancelledEvent,
    OrderRejectedEvent,
    TradeEvent,
    BookUpdateEvent,
)


class OrderService:
    """
    Application service for order management.

    Installed as app.state.order_service during startup — one instance
    per process, shared across all request handlers via DI.
    """

    def __init__(self, engine: MatchingEngine, event_bus: EventBus) -> None:
        self._engine = engine
        self._event_bus = event_bus
        # Wire the engine callback so we receive events synchronously
        self._engine.set_event_callback(self._on_engine_event)

    # ── Hot path callback (synchronous, called from inside submit_order) ──────

    def _on_engine_event(self, event: EngineEvent) -> None:
        """
        Translate an EngineEvent into domain events and fire them onto the bus.

        Rules:
        - Never await anything
        - Never raise (exceptions would propagate into the engine)
        - Use publish_nowait (drops silently if bus is full)
        """
        try:
            sym = event.symbol
            ts = event.timestamp_ns

            if event.event_type == EventType.ORDER_REJECTED and event.result:
                self._event_bus.publish_nowait(OrderRejectedEvent(
                    symbol=sym,
                    order=event.result.order,
                    reason=str(event.reject_reason or "UNKNOWN"),
                    timestamp_ns=ts,
                ))
                return

            result = event.result
            if result:
                order = result.order

                # Emit individual trade events first (price discovery)
                for trade in result.trades:
                    self._event_bus.publish_nowait(TradeEvent(
                        symbol=sym,
                        trade=trade,
                        timestamp_ns=trade.timestamp_ns,
                    ))

                # Emit order status event
                if event.event_type == EventType.ORDER_FILLED:
                    self._event_bus.publish_nowait(OrderFilledEvent(
                        symbol=sym,
                        order=order,
                        trades=result.trades,
                        snapshot=event.snapshot,
                        timestamp_ns=ts,
                    ))
                elif event.event_type == EventType.ORDER_PARTIALLY_FILLED:
                    self._event_bus.publish_nowait(OrderPartiallyFilledEvent(
                        symbol=sym,
                        order=order,
                        trades=result.trades,
                        snapshot=event.snapshot,
                        timestamp_ns=ts,
                    ))
                else:
                    self._event_bus.publish_nowait(OrderAckedEvent(
                        symbol=sym,
                        order=order,
                        snapshot=event.snapshot,
                        timestamp_ns=ts,
                    ))

            if event.cancelled_order:
                self._event_bus.publish_nowait(OrderCancelledEvent(
                    symbol=sym,
                    order=event.cancelled_order,
                    snapshot=event.snapshot,
                    timestamp_ns=ts,
                ))

            # Always propagate book snapshot so the UI stays current
            if event.snapshot:
                self._event_bus.publish_nowait(BookUpdateEvent(
                    symbol=sym,
                    snapshot=event.snapshot,
                    timestamp_ns=ts,
                ))

        except Exception:
            pass  # Protect the engine; swallow all errors here

    # ── Public API ────────────────────────────────────────────────────────────

    def submit(self, order: Order) -> MatchResult:
        """Submit an order to the matching engine. Returns immediately."""
        return self._engine.submit_order(order)

    def cancel(self, order_id: OrderId) -> Optional[Order]:
        """Cancel a resting order. Returns the cancelled order or None."""
        return self._engine.cancel_order(order_id)

    def get_order(self, order_id: OrderId) -> Optional[Order]:
        return self._engine.get_order(order_id)
