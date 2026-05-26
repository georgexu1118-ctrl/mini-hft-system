from __future__ import annotations

from collections.abc import Sequence

import pytest

from engine.core.order import Order, Trade
from engine.core.types import OrderSide, OrderStatus, OrderType
from infra.db.serialization import PersistenceRecords
from infra.db.writer import AsyncDatabaseWriter, WriterConfig
from shared.events import EventBus, OrderFilledEvent, TradeEvent


class RecordingSink:
    def __init__(self, failures: int = 0) -> None:
        self.failures = failures
        self.batches: list[Sequence[PersistenceRecords]] = []

    async def write_batch(self, records: Sequence[PersistenceRecords]) -> None:
        if self.failures:
            self.failures -= 1
            raise ConnectionError("temporary database outage")
        self.batches.append(records)


@pytest.mark.asyncio
async def test_writer_batches_events_retries_and_drains_on_shutdown() -> None:
    bus = EventBus(queue_depth=10)
    sink = RecordingSink(failures=1)
    writer = AsyncDatabaseWriter(
        bus,
        sink,
        WriterConfig(batch_size=10, flush_interval_s=0.001, max_retries=1, retry_backoff_s=0),
    )
    order = Order("AAPL", OrderSide.BUY, OrderType.LIMIT, 5, price=100.0)
    order.status = OrderStatus.FILLED
    trade = Trade("AAPL", 100.0, 5, OrderSide.BUY, "maker", order.order_id)

    bus.publish_nowait(TradeEvent(symbol="AAPL", trade=trade))
    bus.publish_nowait(OrderFilledEvent(symbol="AAPL", order=order, trades=[trade]))
    writer.request_stop()
    await writer.run()

    assert len(sink.batches) == 1
    assert writer.metrics.events_consumed == 2
    assert writer.metrics.rows_written == 2
    assert writer.metrics.write_retries == 1
    assert writer.metrics.failed_batches == 0


def test_persistence_queue_drops_are_attributed_without_blocking_publisher() -> None:
    bus = EventBus(queue_depth=1)
    writer = AsyncDatabaseWriter(bus, RecordingSink())

    bus.publish_nowait(TradeEvent(symbol="AAPL"))
    bus.publish_nowait(TradeEvent(symbol="AAPL"))

    assert writer.dropped_events == 1
    assert bus.dropped_events == 1
