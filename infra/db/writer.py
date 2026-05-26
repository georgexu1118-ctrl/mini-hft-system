"""Bounded asynchronous execution-event persistence service."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol, Sequence

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine

from engine.core.types import EventType
from shared.events import DomainEvent, EventBus

from .schema import orders, trades
from .serialization import PersistenceRecords, serialize_event


class PersistenceSink(Protocol):
    """Replaceable storage adapter boundary (Postgres today, broker later)."""

    async def write_batch(self, records: Sequence[PersistenceRecords]) -> None: ...


class PostgresCoreSink:
    """Batch writes through SQLAlchemy Core using asyncpg without ORM overhead."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def write_batch(self, records: Sequence[PersistenceRecords]) -> None:
        order_rows = [row for item in records for row in item.orders]
        trade_rows = [row for item in records for row in item.trades]
        if not order_rows and not trade_rows:
            return

        async with self._engine.begin() as connection:
            if order_rows:
                statement = insert(orders).values(order_rows)
                excluded = statement.excluded
                await connection.execute(statement.on_conflict_do_update(
                    index_elements=[orders.c.order_id],
                    set_={
                        "status": excluded.status,
                        "filled_quantity": excluded.filled_quantity,
                        "avg_fill_price": excluded.avg_fill_price,
                        "processed_at_ns": excluded.processed_at_ns,
                        "acked_at_ns": excluded.acked_at_ns,
                    },
                ))
            if trade_rows:
                # Trade prints are immutable; replaying a batch is idempotent.
                await connection.execute(
                    insert(trades).values(trade_rows).on_conflict_do_nothing(
                        index_elements=[trades.c.trade_id],
                    )
                )


@dataclass(slots=True)
class PersistenceMetrics:
    events_consumed: int = 0
    rows_written: int = 0
    batches_written: int = 0
    write_retries: int = 0
    failed_batches: int = 0


@dataclass(frozen=True, slots=True)
class WriterConfig:
    batch_size: int = 250
    flush_interval_s: float = 0.025
    max_retries: int = 3
    retry_backoff_s: float = 0.020


class AsyncDatabaseWriter:
    """
    Consume execution events on a bounded EventBus queue and persist in batches.

    The matching callback only performs `put_nowait()`. If PostgreSQL stalls,
    this queue sheds cold-path records and emits a drop metric rather than
    making order acknowledgement latency depend on WAL flush or fsync.
    """

    SUBSCRIBER_NAME = "persistence"

    def __init__(
        self,
        event_bus: EventBus,
        sink: PersistenceSink,
        config: WriterConfig | None = None,
    ) -> None:
        self._event_bus = event_bus
        self._sink = sink
        self._config = config or WriterConfig()
        self._queue = event_bus.subscribe(
            EventType.TRADE,
            EventType.ORDER_ACKED,
            EventType.ORDER_FILLED,
            EventType.ORDER_PARTIALLY_FILLED,
            EventType.ORDER_CANCELLED,
            EventType.ORDER_REJECTED,
            subscriber_name=self.SUBSCRIBER_NAME,
        )
        self.metrics = PersistenceMetrics()
        self._stop_requested = False

    @property
    def dropped_events(self) -> int:
        return self._event_bus.dropped_events_for(self.SUBSCRIBER_NAME)

    async def run(self) -> None:
        """Run until shutdown is requested and every already-queued event flushes."""
        while not self._stop_requested or not self._queue.empty():
            batch = await self._next_batch()
            if batch:
                await self._persist_with_retry(batch)

    def request_stop(self) -> None:
        """Begin graceful drain; called before awaiting the writer task."""
        self._stop_requested = True

    async def _next_batch(self) -> list[PersistenceRecords]:
        try:
            event = await asyncio.wait_for(
                self._queue.get(),
                timeout=self._config.flush_interval_s,
            )
        except TimeoutError:
            return []

        events: list[DomainEvent] = [event]
        while len(events) < self._config.batch_size:
            try:
                events.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break

        self.metrics.events_consumed += len(events)
        return [serialize_event(item) for item in events]

    async def _persist_with_retry(self, batch: list[PersistenceRecords]) -> None:
        for attempt in range(self._config.max_retries + 1):
            try:
                await self._sink.write_batch(batch)
                self.metrics.batches_written += 1
                self.metrics.rows_written += sum(
                    len(item.orders) + len(item.trades) for item in batch
                )
                return
            except Exception:
                if attempt == self._config.max_retries:
                    self.metrics.failed_batches += 1
                    return
                self.metrics.write_retries += 1
                await asyncio.sleep(self._config.retry_backoff_s * (2**attempt))
