from __future__ import annotations

import asyncio
import time

import pytest

from api.services.book_stream_service import BookDeltaProjector
from api.services.metrics_service import MetricsService
from api.services.trade_tape_service import TradeTapeService
from engine.core.matching_engine import MatchingEngine
from engine.core.order import Order, Trade
from engine.core.order_book import BookSnapshot
from engine.core.types import OrderSide, OrderType
from shared.events import EventBus, OrderAckedEvent


def test_book_projection_bootstraps_snapshot_then_sends_changed_levels() -> None:
    projector = BookDeltaProjector()
    initial = BookSnapshot("AAPL", 10, [(100.0, 20, 1)], [(101.0, 15, 1)])
    updated = BookSnapshot("AAPL", 11, [(99.5, 30, 1)], [(101.0, 10, 1)])

    snapshot_message = projector.project(initial)
    delta = projector.project(updated)

    assert snapshot_message.type == "BOOK_SNAPSHOT"
    assert delta.type == "BOOK_DELTA"
    assert delta.previous_sequence == 10
    assert delta.sequence == 11
    assert {(level.price, level.quantity) for level in delta.bids} == {(99.5, 30), (100.0, 0)}
    assert [(level.price, level.quantity) for level in delta.asks] == [(101.0, 10)]


def test_trade_tape_is_newest_first_and_bounded() -> None:
    tape = TradeTapeService(EventBus(), max_trades_per_symbol=2)
    for price in (100.0, 100.1, 100.2):
        tape.record(Trade("AAPL", price, 1, OrderSide.BUY, "maker", "taker"))

    assert [trade.price for trade in tape.recent("aapl")] == [100.2, 100.1]


@pytest.mark.asyncio
async def test_metrics_observes_processing_latency_on_its_cold_path_queue() -> None:
    bus = EventBus()
    metrics = MetricsService(MatchingEngine(), bus)
    task = asyncio.create_task(metrics.run())
    order = Order("AAPL", OrderSide.BUY, OrderType.LIMIT, 1, price=100.0)
    order.received_at_ns = time.time_ns()
    order.processed_at_ns = order.received_at_ns + 25_000

    bus.publish_nowait(OrderAckedEvent(symbol="AAPL", order=order))
    await asyncio.sleep(0)
    snapshot = metrics.snapshot()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    assert snapshot.latency_sample_count == 1
    assert snapshot.p50_us == 25.0
    assert snapshot.p99_us == 25.0
