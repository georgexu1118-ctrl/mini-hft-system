"""
Domain event definitions used across service boundaries.

These are plain dataclasses — fast to construct, zero serialization cost
on the hot path. Pydantic models live in api/websocket/schemas.py and are
constructed only when pushing data to clients.

Event flow:
  MatchingEngine → EngineEvent (callback)
  → OrderService translates to domain events
  → EventBus distributes to subscribers
  → WebSocketBroadcaster serialises to WS messages
  → DatabaseWriter persists asynchronously
"""
from __future__ import annotations

import asyncio
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from engine.core.order import Order, Trade
from engine.core.order_book import BookSnapshot
from engine.core.types import EventType, Symbol, TimestampNs


@dataclass
class DomainEvent:
    """Base class for all domain events."""
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    event_type: EventType = EventType.ORDER_ACKED
    timestamp_ns: TimestampNs = field(default_factory=time.time_ns)
    symbol: Symbol = ""


@dataclass
class OrderAckedEvent(DomainEvent):
    event_type: EventType = EventType.ORDER_ACKED
    order: Optional[Order] = None
    snapshot: Optional[BookSnapshot] = None


@dataclass
class OrderFilledEvent(DomainEvent):
    event_type: EventType = EventType.ORDER_FILLED
    order: Optional[Order] = None
    trades: list[Trade] = field(default_factory=list)
    snapshot: Optional[BookSnapshot] = None


@dataclass
class OrderPartiallyFilledEvent(DomainEvent):
    event_type: EventType = EventType.ORDER_PARTIALLY_FILLED
    order: Optional[Order] = None
    trades: list[Trade] = field(default_factory=list)
    snapshot: Optional[BookSnapshot] = None


@dataclass
class OrderCancelledEvent(DomainEvent):
    event_type: EventType = EventType.ORDER_CANCELLED
    order: Optional[Order] = None
    snapshot: Optional[BookSnapshot] = None


@dataclass
class OrderRejectedEvent(DomainEvent):
    event_type: EventType = EventType.ORDER_REJECTED
    order: Optional[Order] = None
    reason: str = ""


@dataclass
class TradeEvent(DomainEvent):
    event_type: EventType = EventType.TRADE
    trade: Optional[Trade] = None


@dataclass
class BookUpdateEvent(DomainEvent):
    event_type: EventType = EventType.BOOK_UPDATE
    snapshot: Optional[BookSnapshot] = None


@dataclass
class TickerEvent(DomainEvent):
    event_type: EventType = EventType.TICKER
    bid: float = 0.0
    ask: float = 0.0
    last: float = 0.0
    volume: int = 0


# Async subscriber callback type
AsyncSubscriber = Callable[[DomainEvent], Awaitable[None]]


class EventBus:
    """
    In-process async event bus with topic-based fan-out.

    Intentionally simple — no Kafka, no Redis, no broker. Subscribers
    receive events via asyncio queues. This avoids serialisation/network
    overhead on the hot path while still decoupling producers from consumers.

    The core HFT lesson here: external message brokers are for *inter-process*
    or *inter-machine* communication. For intra-process fan-out you want
    shared memory or queue references, not TCP sockets.

    If a subscriber queue fills up, the event is dropped and a counter is
    incremented. In production: alert on sustained drops, backpressure to
    strategy layer, or increase queue depth.
    """

    DEFAULT_QUEUE_DEPTH = 1_000

    def __init__(self, queue_depth: int = DEFAULT_QUEUE_DEPTH) -> None:
        self._queues: dict[EventType, list[asyncio.Queue[DomainEvent]]] = defaultdict(list)
        self._queue_depth = queue_depth
        self._dropped: int = 0
        self._subscriber_names: dict[int, str] = {}
        self._subscriber_dropped: dict[str, int] = defaultdict(int)

    def subscribe(
        self,
        *event_types: EventType,
        subscriber_name: str | None = None,
    ) -> asyncio.Queue[DomainEvent]:
        """
        Subscribe to one or more event types. Returns a queue the caller
        must drain in a background task.
        """
        q: asyncio.Queue[DomainEvent] = asyncio.Queue(maxsize=self._queue_depth)
        if subscriber_name:
            self._subscriber_names[id(q)] = subscriber_name
        for et in event_types:
            self._queues[et].append(q)
        return q

    def publish_nowait(self, event: DomainEvent) -> None:
        """
        Fire-and-forget publish. Called from synchronous code (engine callback).
        Drops silently if any subscriber is slow — prefer alerting over blocking.
        """
        for q in self._queues.get(event.event_type, []):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                self._dropped += 1
                subscriber = self._subscriber_names.get(id(q))
                if subscriber:
                    self._subscriber_dropped[subscriber] += 1

    async def publish(self, event: DomainEvent) -> None:
        """Awaitable publish — use from async code when backpressure is acceptable."""
        for q in self._queues.get(event.event_type, []):
            await q.put(event)

    @property
    def dropped_events(self) -> int:
        return self._dropped

    def dropped_events_for(self, subscriber_name: str) -> int:
        """Return drops attributed to one cold-path consumer queue."""
        return self._subscriber_dropped[subscriber_name]
