"""
HAL conformance test suite.

Every MatchingHAL implementation must pass these tests.
When M7 (C++ HAL) or M8 (FPGA HAL) is added, run this suite
against the new implementation to verify it satisfies the contract.

Usage:
    pytest tests/engine/test_hal_conformance.py -v
    pytest tests/engine/test_hal_conformance.py -v -k "fpga" (M8)
"""
import pytest
from engine.core.types import OrderSide, OrderType, TimeInForce, OrderStatus
from engine.core.order import Order
from engine.hal.abstract import MatchingHAL
from engine.hal.software import SoftwareMatchingHAL


# ── HAL factory — swap this for C++/FPGA HAL to run conformance ──────────────

def make_hal() -> MatchingHAL:
    """Return the HAL under test. Parameterise this for M7/M8."""
    return SoftwareMatchingHAL()


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def hal() -> MatchingHAL:
    h = make_hal()
    h.register_symbol("AAPL")
    return h


def limit_buy(qty: int, price: float, tif: TimeInForce = TimeInForce.GTC) -> Order:
    return Order(symbol="AAPL", side=OrderSide.BUY, order_type=OrderType.LIMIT,
                 quantity=qty, price=price, time_in_force=tif)


def limit_sell(qty: int, price: float, tif: TimeInForce = TimeInForce.GTC) -> Order:
    return Order(symbol="AAPL", side=OrderSide.SELL, order_type=OrderType.LIMIT,
                 quantity=qty, price=price, time_in_force=tif)


# ── Protocol conformance ──────────────────────────────────────────────────────

def test_hal_satisfies_matching_backend_protocol(hal):
    """SoftwareMatchingHAL must satisfy the MatchingBackend protocol."""
    from engine.core.protocols import MatchingBackend
    assert isinstance(hal, MatchingBackend)


def test_register_symbol(hal):
    hal.register_symbol("TSLA")
    assert "TSLA" in hal.registered_symbols
    assert "AAPL" in hal.registered_symbols


def test_submit_resting_limit_order(hal):
    order = limit_buy(100, 150.0)
    result = hal.submit_order(order)
    assert result.is_new_resting
    assert result.trades == []
    assert order.status == OrderStatus.OPEN


def test_submit_matching_orders(hal):
    buy = limit_buy(100, 150.0)
    hal.submit_order(buy)
    sell = limit_sell(100, 150.0)
    result = hal.submit_order(sell)
    assert len(result.trades) == 1
    assert result.trades[0].quantity == 100
    assert buy.status == OrderStatus.FILLED


def test_cancel_resting_order(hal):
    order = limit_buy(100, 150.0)
    hal.submit_order(order)
    cancelled = hal.cancel_order(order.order_id)
    assert cancelled is not None
    assert cancelled.status == OrderStatus.CANCELLED


def test_cancel_nonexistent_order_returns_none(hal):
    assert hal.cancel_order("does-not-exist") is None


def test_get_snapshot_returns_correct_data(hal):
    hal.submit_order(limit_buy(50, 100.0))
    hal.submit_order(limit_sell(50, 101.0))
    snap = hal.get_snapshot("AAPL", depth=5)
    assert snap is not None
    assert snap.best_bid == 100.0
    assert snap.best_ask == 101.0
    assert snap.spread is not None


def test_get_snapshot_unknown_symbol_returns_none(hal):
    assert hal.get_snapshot("UNKNOWN") is None


def test_get_order_by_id(hal):
    order = limit_buy(100, 150.0)
    hal.submit_order(order)
    retrieved = hal.get_order(order.order_id)
    assert retrieved is order  # software HAL returns same object


def test_get_order_unknown_id_returns_none(hal):
    assert hal.get_order("ghost") is None


def test_event_callback_is_called(hal):
    events = []
    hal.set_event_callback(lambda e: events.append(e))
    hal.submit_order(limit_buy(100, 150.0))
    assert len(events) >= 1


def test_timestamps_populated_after_submit(hal):
    order = limit_buy(100, 150.0)
    hal.submit_order(order)
    assert order.received_at_ns > 0
    assert order.processed_at_ns > 0


# ── Binary codec round-trip ───────────────────────────────────────────────────

def test_order_to_bytes_roundtrip():
    """Binary encode/decode must preserve all fields."""
    original = limit_buy(250, 149.99, tif=TimeInForce.IOC)
    encoded = original.to_bytes()
    assert len(encoded) == 64

    decoded = Order.from_bytes(encoded)
    assert decoded.symbol == original.symbol
    assert decoded.side == original.side
    assert decoded.order_type == original.order_type
    assert decoded.quantity == original.quantity
    assert abs(decoded.price - original.price) < 1e-9
    assert decoded.time_in_force == original.time_in_force
    assert decoded.order_id == original.order_id


def test_order_frame_is_cache_line_aligned():
    """ORDER_FRAME must be exactly 64 bytes — one cache line."""
    from engine.core.binary_protocol import ORDER_FRAME
    assert ORDER_FRAME.size == 64


def test_trade_frame_is_cache_line_aligned():
    """TRADE_FRAME must be exactly 64 bytes — one cache line."""
    from engine.core.binary_protocol import TRADE_FRAME
    assert TRADE_FRAME.size == 64


# ── Ring buffer ───────────────────────────────────────────────────────────────

def test_ring_buffer_put_get_roundtrip():
    from engine.core.ring_buffer import SPSCRingBuffer
    ring = SPSCRingBuffer(capacity=8, item_size=64)
    frame = b"\xAB" * 64
    assert ring.put(frame)
    result = ring.get()
    assert result == frame


def test_ring_buffer_full_returns_false():
    from engine.core.ring_buffer import SPSCRingBuffer
    ring = SPSCRingBuffer(capacity=2, item_size=4)
    assert ring.put(b"\x01" * 4)
    assert ring.put(b"\x02" * 4)
    assert not ring.put(b"\x03" * 4)  # full


def test_ring_buffer_empty_returns_none():
    from engine.core.ring_buffer import SPSCRingBuffer
    ring = SPSCRingBuffer(capacity=4, item_size=8)
    assert ring.get() is None


def test_ring_buffer_capacity_is_power_of_2():
    from engine.core.ring_buffer import SPSCRingBuffer
    ring = SPSCRingBuffer(capacity=5)
    assert ring.capacity == 8   # next power of 2


def test_ring_buffer_fifo_order():
    from engine.core.ring_buffer import SPSCRingBuffer
    ring = SPSCRingBuffer(capacity=4, item_size=1)
    for i in range(4):
        ring.put(bytes([i]))
    for i in range(4):
        assert ring.get() == bytes([i])


def test_ring_buffer_wraps_around():
    """Ring buffer must correctly handle the wrap-around case."""
    from engine.core.ring_buffer import SPSCRingBuffer
    ring = SPSCRingBuffer(capacity=4, item_size=1)
    for i in range(4):
        ring.put(bytes([i]))
    ring.get()
    ring.get()
    ring.put(bytes([99]))
    ring.put(bytes([100]))
    assert ring.size() == 4
    assert ring.get() == bytes([2])
    assert ring.get() == bytes([3])
    assert ring.get() == bytes([99])
    assert ring.get() == bytes([100])
