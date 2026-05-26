from __future__ import annotations
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

from sortedcontainers import SortedDict

from .types import OrderSide, OrderStatus, TimeInForce, Price, Quantity, OrderId, Symbol
from .order import Order, Trade


@dataclass(slots=True)
class PriceLevel:
    """
    All resting orders at a single price point, maintained as a FIFO queue.

    This is the fundamental unit of price-time priority matching. Orders at
    the same price are filled oldest-first (time priority). When the front
    order is fully consumed, it is removed in O(1) via popleft.

    Cancel complexity is O(n) here due to the deque scan. In a production
    C++ engine this level would be a doubly-linked intrusive list so that
    cancel is O(1) given a node pointer stored in the cancel map.
    """
    price: Price
    orders: deque[Order] = field(default_factory=deque)
    total_quantity: Quantity = 0

    def add(self, order: Order) -> None:
        self.orders.append(order)
        self.total_quantity += order.remaining_quantity

    def peek(self) -> Optional[Order]:
        return self.orders[0] if self.orders else None

    def remove(self, order_id: OrderId) -> Optional[Order]:
        for i, order in enumerate(self.orders):
            if order.order_id == order_id:
                self.total_quantity -= order.remaining_quantity
                del self.orders[i]
                return order
        return None

    def is_empty(self) -> bool:
        return not self.orders

    @property
    def order_count(self) -> int:
        return len(self.orders)


@dataclass
class BookSnapshot:
    """
    Point-in-time, depth-limited view of the order book.

    Sent over WebSocket on every change. Level 2 data: price, aggregate
    quantity, and order count at each price level. Used by the frontend
    for the ladder/depth visualisation.

    sequence is a monotonically increasing counter per symbol — clients
    can detect missed updates by checking for gaps.
    """
    symbol: Symbol
    sequence: int
    # Each tuple: (price, total_qty, order_count) sorted best-first
    bids: list[tuple[Price, Quantity, int]]
    asks: list[tuple[Price, Quantity, int]]
    timestamp_ns: int = field(default_factory=time.time_ns)

    @property
    def best_bid(self) -> Optional[Price]:
        return self.bids[0][0] if self.bids else None

    @property
    def best_ask(self) -> Optional[Price]:
        return self.asks[0][0] if self.asks else None

    @property
    def spread(self) -> Optional[float]:
        if self.best_bid and self.best_ask:
            return round(self.best_ask - self.best_bid, 10)
        return None

    @property
    def mid_price(self) -> Optional[float]:
        if self.best_bid and self.best_ask:
            return (self.best_bid + self.best_ask) / 2.0
        return None


@dataclass
class MatchResult:
    """Returned by OrderBook.submit() to the engine and then to the service layer."""
    order: Order
    trades: list[Trade] = field(default_factory=list)
    # True when the residual of the order was added as a resting order
    is_new_resting: bool = False

    @property
    def total_filled(self) -> Quantity:
        return sum(t.quantity for t in self.trades)

    @property
    def avg_fill_price(self) -> Optional[float]:
        if not self.trades:
            return None
        total_notional = sum(t.price * t.quantity for t in self.trades)
        return total_notional / self.total_filled


class OrderBook:
    """
    Central Limit Order Book (CLOB) for a single instrument.

    Implements price-time priority (FIFO): the best price matches first;
    at the same price, the earliest resting order matches first. This is
    the algorithm used on NYSE, NASDAQ, CME, and most global exchanges.

    Internal data structures
    ─────────────────────────
    Bids  SortedDict[price → PriceLevel]  sorted descending (highest first)
    Asks  SortedDict[price → PriceLevel]  sorted ascending  (lowest first)

    SortedDict uses a skip-list under the hood (sortedcontainers).
    O(log n) insert/delete where n = number of distinct price levels.

    The cancel_map gives O(1) lookup of (side, price) for any order_id,
    reducing cancel to O(log n) + O(m) where m = orders at that level.

    Production notes
    ─────────────────
    In C++: std::map<Price, PriceLevel> with a custom arena allocator per
    symbol. Each PriceLevel uses an intrusive doubly-linked list so cancel
    is O(1) given the node pointer stored in the cancel map.

    In FPGA: price levels live in BRAM as direct-mapped arrays indexed by
    price tick. Matching walks the array from best price outward. The
    entire critical path (submit → match → ack) fits in < 1 µs.
    """

    def __init__(self, symbol: Symbol) -> None:
        self.symbol = symbol
        self.sequence: int = 0

        # lambda k: -k makes SortedDict sort prices descending (highest bid first)
        self._bids: SortedDict = SortedDict(lambda k: -k)
        # Default ascending sort — lowest ask first
        self._asks: SortedDict = SortedDict()

        # order_id → (side, price) for O(1) cancel lookup
        self._cancel_map: dict[OrderId, tuple[OrderSide, Price]] = {}

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _side_book(self, side: OrderSide) -> SortedDict:
        return self._bids if side == OrderSide.BUY else self._asks

    def _opposite_book(self, side: OrderSide) -> SortedDict:
        return self._asks if side == OrderSide.BUY else self._bids

    def _crosses(self, incoming_side: OrderSide, incoming_price: Optional[Price], level_price: Price) -> bool:
        """True if the incoming order's price is aggressive enough to match this level."""
        if incoming_price is None:
            return True  # MARKET orders always cross
        if incoming_side == OrderSide.BUY:
            return incoming_price >= level_price
        return incoming_price <= level_price

    def _rest_order(self, order: Order) -> None:
        """Place a resting limit order into the appropriate side of the book."""
        book = self._side_book(order.side)
        price = order.price
        if price not in book:
            book[price] = PriceLevel(price=price)
        book[price].add(order)
        self._cancel_map[order.order_id] = (order.side, price)
        # A residual may rest after an execution; preserve PARTIALLY_FILLED so
        # downstream risk and persistence consumers observe the executed state.
        if order.filled_quantity == 0:
            order.status = OrderStatus.OPEN

    # ── Public API ────────────────────────────────────────────────────────────

    def submit(self, order: Order) -> MatchResult:
        """
        Submit an order: attempt matching, then rest residual per TIF policy.

        This is the hot path. Zero I/O, zero allocations on the critical path
        beyond the Trade objects created for each fill. Order state is mutated
        in-place to avoid extra allocation.

        Matching loop logic
        ────────────────────
        1. Peek at the best opposite price level.
        2. If the incoming order's price doesn't cross it, stop.
        3. Walk the FIFO queue at that level, consuming fills until one side
           is exhausted or the level runs dry.
        4. Remove empty price levels. Repeat from step 1.
        5. Apply TIF policy to any residual quantity.
        """
        self.sequence += 1
        result = MatchResult(order=order)
        opposite = self._opposite_book(order.side)

        # ── Matching loop ─────────────────────────────────────────────────
        while order.remaining_quantity > 0 and opposite:
            best_price, price_level = opposite.peekitem(0)

            if not self._crosses(order.side, order.price, best_price):
                break

            while order.remaining_quantity > 0 and not price_level.is_empty():
                resting = price_level.peek()
                fill_qty = min(order.remaining_quantity, resting.remaining_quantity)
                # Maker's price determines the trade price (standard exchange rule)
                trade_price = resting.price

                order.filled_quantity += fill_qty
                resting.filled_quantity += fill_qty
                price_level.total_quantity -= fill_qty

                result.trades.append(Trade(
                    symbol=self.symbol,
                    price=trade_price,
                    quantity=fill_qty,
                    aggressor_side=order.side,
                    maker_order_id=resting.order_id,
                    taker_order_id=order.order_id,
                ))

                if resting.remaining_quantity == 0:
                    resting.status = OrderStatus.FILLED
                    price_level.orders.popleft()
                    self._cancel_map.pop(resting.order_id, None)
                else:
                    resting.status = OrderStatus.PARTIALLY_FILLED

            if price_level.is_empty():
                del opposite[best_price]

        # ── Update status ─────────────────────────────────────────────────
        order.processed_at_ns = time.time_ns()

        if order.remaining_quantity == 0:
            order.status = OrderStatus.FILLED
        elif order.filled_quantity > 0:
            order.status = OrderStatus.PARTIALLY_FILLED

        if result.trades:
            order.avg_fill_price = result.avg_fill_price

        # ── TIF policy for residual ───────────────────────────────────────
        if order.remaining_quantity > 0 and order.is_limit:
            match order.time_in_force:
                case TimeInForce.GTC | TimeInForce.GTD:
                    self._rest_order(order)
                    result.is_new_resting = True
                case TimeInForce.IOC:
                    order.status = OrderStatus.CANCELLED
                case TimeInForce.FOK:
                    # FOK: partial fill means we must cancel entire order.
                    # A correct implementation checks liquidity *before* matching
                    # to avoid partial fills entirely. This is the simplified path.
                    order.status = OrderStatus.CANCELLED

        return result

    def cancel(self, order_id: OrderId) -> Optional[Order]:
        """
        Cancel a resting order by ID.

        O(log n + m) — log n to locate the price level, m to scan the
        deque at that level. In production (intrusive list): O(log n).
        """
        if order_id not in self._cancel_map:
            return None

        side, price = self._cancel_map.pop(order_id)
        book = self._side_book(side)

        if price not in book:
            return None

        price_level: PriceLevel = book[price]
        order = price_level.remove(order_id)

        if price_level.is_empty():
            del book[price]

        if order:
            order.status = OrderStatus.CANCELLED

        return order

    def snapshot(self, depth: int = 10) -> BookSnapshot:
        """Depth-limited snapshot for market data dissemination. O(depth)."""
        bids_out: list[tuple[Price, Quantity, int]] = []
        for price, level in list(self._bids.items())[:depth]:
            bids_out.append((price, level.total_quantity, level.order_count))

        asks_out: list[tuple[Price, Quantity, int]] = []
        for price, level in list(self._asks.items())[:depth]:
            asks_out.append((price, level.total_quantity, level.order_count))

        return BookSnapshot(
            symbol=self.symbol,
            sequence=self.sequence,
            bids=bids_out,
            asks=asks_out,
        )

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def best_bid(self) -> Optional[Price]:
        return self._bids.peekitem(0)[0] if self._bids else None

    @property
    def best_ask(self) -> Optional[Price]:
        return self._asks.peekitem(0)[0] if self._asks else None

    @property
    def spread(self) -> Optional[float]:
        bb, ba = self.best_bid, self.best_ask
        return round(ba - bb, 10) if bb and ba else None

    @property
    def mid_price(self) -> Optional[float]:
        bb, ba = self.best_bid, self.best_ask
        return (bb + ba) / 2.0 if bb and ba else None

    def bid_volume(self) -> Quantity:
        return sum(lvl.total_quantity for lvl in self._bids.values())

    def ask_volume(self) -> Quantity:
        return sum(lvl.total_quantity for lvl in self._asks.values())
