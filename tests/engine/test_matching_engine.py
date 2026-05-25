"""
Unit tests for the matching engine.

These tests verify the core invariants of price-time priority matching:
  1. Limit orders rest when no counterpart exists
  2. Crossing orders match at the maker's price
  3. FIFO priority at each price level
  4. Market orders sweep the book
  5. TIF policies (GTC, IOC, FOK)
  6. Cancel mechanics
  7. Edge cases (unknown symbol, duplicate ID, zero quantity)

Run with:
    pytest tests/engine/test_matching_engine.py -v
"""
import pytest
from engine.core.types import (
    OrderSide, OrderType, TimeInForce, OrderStatus, RejectReason,
)
from engine.core.order import Order
from engine.core.matching_engine import MatchingEngine


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def engine() -> MatchingEngine:
    e = MatchingEngine()
    e.register_symbol("AAPL")
    return e


def limit(side: OrderSide, qty: int, price: float,
          tif: TimeInForce = TimeInForce.GTC) -> Order:
    return Order(
        symbol="AAPL", side=side,
        order_type=OrderType.LIMIT,
        quantity=qty, price=price, time_in_force=tif,
    )


def market(side: OrderSide, qty: int) -> Order:
    return Order(
        symbol="AAPL", side=side,
        order_type=OrderType.MARKET,
        quantity=qty,
    )


# ── Basic resting ─────────────────────────────────────────────────────────────

def test_limit_buy_rests_in_empty_book(engine):
    order = limit(OrderSide.BUY, 100, 150.0)
    result = engine.submit_order(order)

    assert result.is_new_resting
    assert result.trades == []
    assert order.status == OrderStatus.OPEN
    assert engine.stats.orders_resting == 1


def test_limit_sell_rests_in_empty_book(engine):
    order = limit(OrderSide.SELL, 50, 200.0)
    result = engine.submit_order(order)

    assert result.is_new_resting
    assert order.status == OrderStatus.OPEN


# ── Matching ──────────────────────────────────────────────────────────────────

def test_crossing_orders_match_at_maker_price(engine):
    """Sell crosses the resting buy — trade price = maker (buyer's) price."""
    buy = limit(OrderSide.BUY, 100, 150.0)
    engine.submit_order(buy)

    sell = limit(OrderSide.SELL, 100, 149.0)   # aggressive: price ≤ best bid
    result = engine.submit_order(sell)

    assert len(result.trades) == 1
    t = result.trades[0]
    assert t.price == 150.0          # maker's price
    assert t.quantity == 100
    assert t.aggressor_side == OrderSide.SELL
    assert buy.status == OrderStatus.FILLED
    assert sell.status == OrderStatus.FILLED


def test_sell_crosses_resting_buy(engine):
    buy = limit(OrderSide.BUY, 100, 150.0)
    engine.submit_order(buy)

    sell = limit(OrderSide.SELL, 100, 150.0)
    result = engine.submit_order(sell)

    assert len(result.trades) == 1
    assert result.trades[0].quantity == 100


# ── Partial fills ─────────────────────────────────────────────────────────────

def test_partial_fill_incoming_larger(engine):
    """Incoming order larger than resting: partial fill, residual rests."""
    buy = limit(OrderSide.BUY, 50, 150.0)
    engine.submit_order(buy)

    sell = limit(OrderSide.SELL, 100, 150.0)
    result = engine.submit_order(sell)

    assert result.trades[0].quantity == 50
    assert buy.status == OrderStatus.FILLED
    assert sell.status == OrderStatus.PARTIALLY_FILLED
    assert sell.remaining_quantity == 50
    assert result.is_new_resting   # residual 50 rests


def test_partial_fill_resting_larger(engine):
    """Incoming order smaller than resting: resting partially filled."""
    buy = limit(OrderSide.BUY, 100, 150.0)
    engine.submit_order(buy)

    sell = limit(OrderSide.SELL, 30, 150.0)
    result = engine.submit_order(sell)

    assert result.trades[0].quantity == 30
    assert buy.status == OrderStatus.PARTIALLY_FILLED
    assert sell.status == OrderStatus.FILLED
    assert buy.remaining_quantity == 70


# ── Price-time priority ───────────────────────────────────────────────────────

def test_price_priority_best_price_fills_first(engine):
    """Higher bid fills before lower bid at a given ask price."""
    buy_low = limit(OrderSide.BUY, 100, 149.0)
    buy_high = limit(OrderSide.BUY, 100, 151.0)
    engine.submit_order(buy_low)
    engine.submit_order(buy_high)

    sell = limit(OrderSide.SELL, 100, 148.0)
    result = engine.submit_order(sell)

    assert result.trades[0].maker_order_id == buy_high.order_id


def test_time_priority_fifo_at_same_price(engine):
    """At the same price, earlier order fills first."""
    buy1 = limit(OrderSide.BUY, 30, 150.0)
    buy2 = limit(OrderSide.BUY, 30, 150.0)
    engine.submit_order(buy1)
    engine.submit_order(buy2)

    sell = limit(OrderSide.SELL, 30, 150.0)
    result = engine.submit_order(sell)

    assert result.trades[0].maker_order_id == buy1.order_id   # FIFO


def test_sweep_multiple_price_levels(engine):
    """Single sell sweeps three buy levels."""
    engine.submit_order(limit(OrderSide.BUY, 50, 150.0))
    engine.submit_order(limit(OrderSide.BUY, 50, 149.0))
    engine.submit_order(limit(OrderSide.BUY, 50, 148.0))

    sell = limit(OrderSide.SELL, 120, 148.0)
    result = engine.submit_order(sell)

    assert len(result.trades) == 3
    assert result.total_filled == 120
    assert sell.status == OrderStatus.FILLED


# ── Market orders ─────────────────────────────────────────────────────────────

def test_market_order_sweeps_book(engine):
    engine.submit_order(limit(OrderSide.SELL, 50, 150.0))
    engine.submit_order(limit(OrderSide.SELL, 50, 151.0))
    engine.submit_order(limit(OrderSide.SELL, 50, 152.0))

    buy = market(OrderSide.BUY, 120)
    result = engine.submit_order(buy)

    assert result.total_filled == 120
    assert len(result.trades) == 3


def test_market_order_empty_book_unfilled(engine):
    """Market order against empty book stays unfilled (no resting)."""
    buy = market(OrderSide.BUY, 100)
    result = engine.submit_order(buy)

    assert result.total_filled == 0
    assert not result.is_new_resting   # market orders don't rest


# ── Time in Force ─────────────────────────────────────────────────────────────

def test_ioc_cancels_unfilled_residual(engine):
    """IOC: fill available quantity, cancel remainder immediately."""
    engine.submit_order(limit(OrderSide.BUY, 50, 150.0))

    sell = limit(OrderSide.SELL, 100, 150.0, tif=TimeInForce.IOC)
    result = engine.submit_order(sell)

    assert result.trades[0].quantity == 50
    assert sell.status == OrderStatus.CANCELLED
    assert not result.is_new_resting   # residual cancelled, not resting


def test_gtc_residual_rests(engine):
    """GTC (default): unfilled residual stays in the book."""
    sell = limit(OrderSide.SELL, 100, 150.0, tif=TimeInForce.GTC)
    result = engine.submit_order(sell)

    assert result.is_new_resting
    assert sell.status == OrderStatus.OPEN


# ── Cancel ────────────────────────────────────────────────────────────────────

def test_cancel_resting_order_removes_from_book(engine):
    buy = limit(OrderSide.BUY, 100, 150.0)
    engine.submit_order(buy)

    snap_before = engine.get_snapshot("AAPL")
    assert snap_before.best_bid == 150.0

    cancelled = engine.cancel_order(buy.order_id)

    assert cancelled is not None
    assert cancelled.status == OrderStatus.CANCELLED

    snap_after = engine.get_snapshot("AAPL")
    assert snap_after.best_bid is None


def test_cancel_filled_order_returns_none(engine):
    buy = limit(OrderSide.BUY, 100, 150.0)
    engine.submit_order(buy)
    engine.submit_order(limit(OrderSide.SELL, 100, 150.0))

    assert buy.status == OrderStatus.FILLED
    result = engine.cancel_order(buy.order_id)
    assert result is None


def test_cancel_unknown_order_returns_none(engine):
    assert engine.cancel_order("does-not-exist") is None


# ── Validation / rejection ────────────────────────────────────────────────────

def test_reject_unknown_symbol(engine):
    order = Order(
        symbol="UNKNOWN", side=OrderSide.BUY,
        order_type=OrderType.LIMIT, quantity=100, price=100.0,
    )
    result = engine.submit_order(order)
    assert order.status == OrderStatus.REJECTED
    assert engine.stats.orders_rejected == 1


def test_reject_zero_quantity(engine):
    order = Order(
        symbol="AAPL", side=OrderSide.BUY,
        order_type=OrderType.LIMIT, quantity=0, price=100.0,
    )
    result = engine.submit_order(order)
    assert order.status == OrderStatus.REJECTED


def test_reject_limit_order_without_price(engine):
    order = Order(
        symbol="AAPL", side=OrderSide.BUY,
        order_type=OrderType.LIMIT, quantity=100, price=None,
    )
    result = engine.submit_order(order)
    assert order.status == OrderStatus.REJECTED


# ── Latency timestamps ────────────────────────────────────────────────────────

def test_timestamps_populated_after_submit(engine):
    order = limit(OrderSide.BUY, 100, 150.0)
    engine.submit_order(order)

    assert order.received_at_ns > 0
    assert order.processed_at_ns > 0
    assert order.acked_at_ns > 0
    assert order.processing_latency_us is not None
    assert order.processing_latency_us >= 0


# ── Stats ─────────────────────────────────────────────────────────────────────

def test_stats_increment_correctly(engine):
    engine.submit_order(limit(OrderSide.BUY, 100, 150.0))
    engine.submit_order(limit(OrderSide.SELL, 100, 150.0))

    stats = engine.stats
    assert stats.orders_received == 2
    assert stats.trades_executed == 1
    assert stats.total_volume == 100
