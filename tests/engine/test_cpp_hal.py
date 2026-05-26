"""Native HAL integration and semantic equivalence tests."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Callable

import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from engine.core.binary_protocol import ORDER_FRAME
from engine.core.order import Order
from engine.core.types import OrderSide, OrderStatus, OrderType, TimeInForce
from engine.hal.abstract import MatchingHAL
from engine.hal.cpp_hal import CppMatchingHAL, is_available
from engine.hal.software import SoftwareMatchingHAL
from infra.config.settings import settings

pytestmark = pytest.mark.skipif(not is_available(), reason="hft_engine_cpp is not built")


@dataclass(frozen=True, slots=True)
class OrderInput:
    side: OrderSide
    order_type: OrderType
    quantity: int
    price: float | None
    tif: TimeInForce = TimeInForce.GTC


STREAM = (
    OrderInput(OrderSide.SELL, OrderType.LIMIT, 50, 101.0),
    OrderInput(OrderSide.SELL, OrderType.LIMIT, 75, 102.0),
    OrderInput(OrderSide.BUY, OrderType.LIMIT, 80, 102.0),
    OrderInput(OrderSide.BUY, OrderType.LIMIT, 25, 100.0),
    OrderInput(OrderSide.SELL, OrderType.LIMIT, 15, 100.0, TimeInForce.IOC),
)


def _order(spec: OrderInput, index: int) -> Order:
    return Order(
        symbol="AAPL",
        side=spec.side,
        order_type=spec.order_type,
        quantity=spec.quantity,
        price=spec.price,
        time_in_force=spec.tif,
        order_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, f"m7-semantic-{index}")),
        created_at_ns=1_000_000 + index,
    )


def _run(factory: Callable[[], MatchingHAL]) -> tuple[list[tuple], tuple, OrderStatus]:
    hal = factory()
    hal.register_symbol("AAPL")
    results: list[tuple] = []
    orders: list[Order] = []
    for index, spec in enumerate(STREAM):
        order = _order(spec, index)
        orders.append(order)
        result = hal.submit_order(order)
        fills = tuple(
            (
                trade.price,
                trade.quantity,
                trade.aggressor_side,
                trade.maker_order_id,
                trade.taker_order_id,
            )
            for trade in result.trades
        )
        results.append((order.status, order.filled_quantity, result.is_new_resting, fills))
    cancelled = hal.cancel_order(orders[3].order_id)
    assert cancelled is not None
    snapshot = hal.get_snapshot("AAPL", depth=10)
    assert snapshot is not None
    depth = (tuple(snapshot.bids), tuple(snapshot.asks), snapshot.sequence)
    return results, depth, cancelled.status


def test_cpp_matches_python_price_time_semantics() -> None:
    """Native execution must preserve fills, status, cancellation, and depth."""
    assert _run(CppMatchingHAL) == _run(SoftwareMatchingHAL)


def test_cpp_order_frame_round_trip_preserves_wire_fields() -> None:
    """ORDER_FRAME is decoded explicitly rather than mapped as native memory."""
    hal = CppMatchingHAL()
    hal.register_symbol("AAPL")
    order = _order(OrderInput(OrderSide.BUY, OrderType.LIMIT, 17, 149.75), 22)
    assert len(order.to_bytes()) == ORDER_FRAME.size == 64
    result = hal.submit_order(order)
    assert result.is_new_resting
    retrieved = hal.get_order(order.order_id)
    assert retrieved is order
    assert retrieved.quantity == 17
    assert retrieved.price == 149.75
    assert retrieved.side == OrderSide.BUY
    assert retrieved.status == OrderStatus.OPEN


def test_cpp_rejects_symbols_that_do_not_fit_wire_abi() -> None:
    hal = CppMatchingHAL()
    with pytest.raises(ValueError, match="1 to 6 ASCII"):
        hal.register_symbol("TOO-LONG")


def test_cpp_emits_backend_neutral_events_and_updates_resting_maker() -> None:
    events = []
    hal = CppMatchingHAL(on_event=events.append)
    hal.register_symbol("AAPL")
    maker = _order(OrderInput(OrderSide.BUY, OrderType.LIMIT, 10, 100.0), 31)
    taker = _order(OrderInput(OrderSide.SELL, OrderType.LIMIT, 10, 100.0), 32)
    hal.submit_order(maker)
    hal.submit_order(taker)
    assert maker.status == OrderStatus.FILLED
    assert len(events) == 2
    assert events[-1].result is not None
    assert events[-1].result.trades[0].maker_order_id == maker.order_id


def test_cpp_backend_runs_through_fastapi_composition(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "persistence_enabled", False)
    monkeypatch.setattr(settings, "matching_backend", "cpp")
    with TestClient(create_app()) as client:
        response = client.post("/api/v1/orders/", json={
            "symbol": "AAPL",
            "side": "BUY",
            "order_type": "LIMIT",
            "quantity": 10,
            "price": 100.0,
        })
        assert response.status_code == 201
        assert client.get("/health").json()["matching_backend"] == "cpp"
