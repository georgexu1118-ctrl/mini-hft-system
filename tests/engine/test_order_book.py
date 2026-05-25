"""
Unit tests for OrderBook internals.

Tests the data structure properties independently of the engine:
  - Bid/ask sorting (best price always at front)
  - Spread and mid-price calculations
  - Snapshot depth limiting
  - Cancel removes from correct side and price level
  - Book cleans up empty price levels
"""
import pytest
from engine.core.types import OrderSide, OrderType, TimeInForce, OrderStatus
from engine.core.order import Order
from engine.core.order_book import OrderBook, PriceLevel


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def book() -> OrderBook:
    return OrderBook("AAPL")


def buy(qty: int, price: float) -> Order:
    return Order(
        symbol="AAPL", side=OrderSide.BUY,
        order_type=OrderType.LIMIT, quantity=qty, price=price,
        time_in_force=TimeInForce.GTC,
    )


def sell(qty: int, price: float) -> Order:
    return Order(
        symbol="AAPL", side=OrderSide.SELL,
        order_type=OrderType.LIMIT, quantity=qty, price=price,
        time_in_force=TimeInForce.GTC,
    )


# ── Bid side ordering ─────────────────────────────────────────────────────────

def test_best_bid_is_highest_price(book):
    book.submit(buy(10, 100.0))
    book.submit(buy(10, 102.0))
    book.submit(buy(10, 101.0))
    assert book.best_bid == 102.0


def test_bids_in_descending_order(book):
    prices = [98.0, 100.0, 99.0, 101.0]
    for p in prices:
        book.submit(buy(10, p))
    snap = book.snapshot(depth=4)
    bid_prices = [lvl[0] for lvl in snap.bids]
    assert bid_prices == sorted(prices, reverse=True)


# ── Ask side ordering ─────────────────────────────────────────────────────────

def test_best_ask_is_lowest_price(book):
    book.submit(sell(10, 105.0))
    book.submit(sell(10, 103.0))
    book.submit(sell(10, 104.0))
    assert book.best_ask == 103.0


def test_asks_in_ascending_order(book):
    prices = [106.0, 104.0, 105.0, 103.0]
    for p in prices:
        book.submit(sell(10, p))
    snap = book.snapshot(depth=4)
    ask_prices = [lvl[0] for lvl in snap.asks]
    assert ask_prices == sorted(prices)


# ── Spread / mid ──────────────────────────────────────────────────────────────

def test_spread_calculation(book):
    book.submit(buy(10, 99.0))
    book.submit(sell(10, 101.0))
    assert abs(book.spread - 2.0) < 1e-9


def test_mid_price_calculation(book):
    book.submit(buy(10, 99.0))
    book.submit(sell(10, 101.0))
    assert abs(book.mid_price - 100.0) < 1e-9


def test_empty_book_returns_none(book):
    assert book.best_bid is None
    assert book.best_ask is None
    assert book.spread is None
    assert book.mid_price is None


# ── Snapshot ──────────────────────────────────────────────────────────────────

def test_snapshot_limits_depth(book):
    for p in range(100, 110):     # 10 bid levels
        book.submit(buy(10, float(p)))
    snap = book.snapshot(depth=3)
    assert len(snap.bids) == 3


def test_snapshot_aggregates_quantities(book):
    """Two orders at same price → one level with combined quantity."""
    book.submit(buy(30, 100.0))
    book.submit(buy(70, 100.0))
    snap = book.snapshot()
    assert snap.bids[0][1] == 100   # total_quantity
    assert snap.bids[0][2] == 2     # order_count


def test_snapshot_sequence_increments(book):
    book.submit(buy(10, 100.0))
    s1 = book.snapshot().sequence
    book.submit(buy(10, 101.0))
    s2 = book.snapshot().sequence
    assert s2 > s1


# ── Cancel ────────────────────────────────────────────────────────────────────

def test_cancel_removes_order(book):
    order = buy(100, 150.0)
    book.submit(order)
    assert book.best_bid == 150.0

    cancelled = book.cancel(order.order_id)
    assert cancelled is not None
    assert cancelled.status == OrderStatus.CANCELLED
    assert book.best_bid is None


def test_cancel_cleans_up_empty_level(book):
    order = buy(100, 150.0)
    book.submit(order)
    book.cancel(order.order_id)

    # Price level 150.0 should be gone from the internal dict
    assert 150.0 not in book._bids


def test_cancel_unknown_id_returns_none(book):
    assert book.cancel("nonexistent") is None


def test_cancel_middle_order_in_queue(book):
    """Cancel middle order; FIFO queue still intact for remaining."""
    o1 = buy(10, 100.0)
    o2 = buy(20, 100.0)
    o3 = buy(30, 100.0)
    book.submit(o1)
    book.submit(o2)
    book.submit(o3)

    book.cancel(o2.order_id)

    snap = book.snapshot()
    # total_quantity should reflect o1 + o3 only
    assert snap.bids[0][1] == 40
    assert snap.bids[0][2] == 2


# ── PriceLevel unit tests ─────────────────────────────────────────────────────

def test_price_level_add_and_total():
    lvl = PriceLevel(price=100.0)
    o = buy(50, 100.0)
    lvl.add(o)
    assert lvl.total_quantity == 50
    assert lvl.order_count == 1


def test_price_level_remove():
    lvl = PriceLevel(price=100.0)
    o1, o2 = buy(30, 100.0), buy(20, 100.0)
    lvl.add(o1)
    lvl.add(o2)

    removed = lvl.remove(o1.order_id)
    assert removed is o1
    assert lvl.total_quantity == 20
    assert not lvl.is_empty()
