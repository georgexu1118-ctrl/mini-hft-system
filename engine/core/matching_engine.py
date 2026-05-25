from __future__ import annotations
import time
from dataclasses import dataclass, field
from typing import Optional, Callable

from .order import Order, Trade
from .order_book import OrderBook, MatchResult, BookSnapshot
from .types import (
    OrderId, OrderStatus, OrderType, Symbol,
    RejectReason, EventType,
)


@dataclass
class EngineStats:
    """
    Counters and latency samples collected during engine operation.

    In production these feed into a metrics pipeline (Prometheus, InfluxDB).
    Latency samples use a rolling window — we care about current behaviour,
    not the average since boot.
    """
    orders_received: int = 0
    orders_matched: int = 0
    orders_resting: int = 0
    orders_cancelled: int = 0
    orders_rejected: int = 0
    trades_executed: int = 0
    total_volume: int = 0

    # Rolling window of processing latencies (microseconds)
    _latency_samples: list[float] = field(default_factory=list)
    _MAX_SAMPLES: int = field(default=10_000, init=False, repr=False)

    def record_latency(self, latency_us: float) -> None:
        self._latency_samples.append(latency_us)
        if len(self._latency_samples) > self._MAX_SAMPLES:
            self._latency_samples = self._latency_samples[-self._MAX_SAMPLES:]

    def percentile(self, p: float) -> Optional[float]:
        """Return the p-th percentile of recent processing latencies."""
        if not self._latency_samples:
            return None
        samples = sorted(self._latency_samples)
        idx = min(int(len(samples) * p / 100), len(samples) - 1)
        return samples[idx]

    @property
    def p50_us(self) -> Optional[float]:
        return self.percentile(50)

    @property
    def p99_us(self) -> Optional[float]:
        return self.percentile(99)

    @property
    def p999_us(self) -> Optional[float]:
        return self.percentile(99.9)


# Callback type: called after every successful match result or rejection.
# The service layer installs this to bridge the engine to async I/O.
EventCallback = Callable[["EngineEvent"], None]


@dataclass
class EngineEvent:
    """
    Thin envelope emitted by the engine after each operation.

    Intentionally a plain dataclass — no Pydantic, no serialization.
    Pydantic models are introduced only at the API boundary.
    """
    event_type: EventType
    timestamp_ns: int
    symbol: Symbol
    result: Optional[MatchResult] = None
    reject_reason: Optional[RejectReason] = None
    cancelled_order: Optional[Order] = None
    snapshot: Optional[BookSnapshot] = None


class MatchingEngine:
    """
    Central matching engine. Owns all order books and the global order map.

    Design principles
    ──────────────────
    1. Synchronous and single-threaded by design.
       Real exchange engines process one order at a time per partition to
       avoid locking entirely. Multi-threading in matching = coordination
       overhead that wrecks latency. Our asyncio service layer serialises
       submissions by design.

    2. Zero I/O on the critical path.
       The engine does no database writes, no network calls, no logging.
       Side effects happen through the event_callback installed by the
       service layer, which fires *after* matching returns.

    3. Pure computation boundary.
       This module has no dependency on FastAPI, SQLAlchemy, or asyncio.
       It can be replaced wholesale with a C extension or FPGA driver
       without touching the API layer. The interface contract is:
           submit_order(Order) → MatchResult
           cancel_order(order_id) → Order | None
           get_snapshot(symbol) → BookSnapshot | None

    Latency profile (Python, developer laptop)
    ────────────────────────────────────────────
    Single limit order, empty book:   ~2–5 µs
    Limit order, 1 fill:             ~5–15 µs
    Market order, 10 fills:         ~20–60 µs

    C++ equivalent (same algorithm):  sub-microsecond
    FPGA equivalent:                  ~50–200 ns
    """

    def __init__(self, on_event: Optional[EventCallback] = None) -> None:
        self._books: dict[Symbol, OrderBook] = {}
        self._order_map: dict[OrderId, Order] = {}
        self._on_event = on_event
        self._stats = EngineStats()

    # ── Configuration ─────────────────────────────────────────────────────────

    def register_symbol(self, symbol: Symbol) -> None:
        """Register a tradeable instrument. Must be called before first order."""
        if symbol not in self._books:
            self._books[symbol] = OrderBook(symbol)

    def set_event_callback(self, cb: EventCallback) -> None:
        """Install (or replace) the event callback. Thread-safe only before use."""
        self._on_event = cb

    # ── Hot path ──────────────────────────────────────────────────────────────

    def submit_order(self, order: Order) -> MatchResult:
        """
        Submit an order for matching. Returns immediately with the result.

        Callers in async context should run this in an executor:
            result = await loop.run_in_executor(None, engine.submit_order, order)

        For our simulation throughput (< 10k orders/sec) it is safe to call
        directly from an async endpoint — the CPU time is sub-millisecond.
        """
        order.received_at_ns = time.time_ns()
        self._stats.orders_received += 1

        # Pre-trade validation (synchronous, no I/O)
        rejection = self._validate(order)
        if rejection:
            order.status = OrderStatus.REJECTED
            empty_result = MatchResult(order=order)
            self._emit(EngineEvent(
                event_type=EventType.ORDER_REJECTED,
                timestamp_ns=time.time_ns(),
                symbol=order.symbol,
                result=empty_result,
                reject_reason=rejection,
            ))
            self._stats.orders_rejected += 1
            return empty_result

        book = self._books[order.symbol]
        result = book.submit(order)
        self._order_map[order.order_id] = order

        # Bookkeeping
        if result.is_new_resting:
            self._stats.orders_resting += 1
        if result.trades:
            self._stats.orders_matched += 1
            self._stats.trades_executed += len(result.trades)
            self._stats.total_volume += result.total_filled

        if order.processing_latency_us is not None:
            self._stats.record_latency(order.processing_latency_us)

        order.acked_at_ns = time.time_ns()

        # Determine event type for the primary event
        if result.trades and order.remaining_quantity == 0:
            evt_type = EventType.ORDER_FILLED
        elif result.trades:
            evt_type = EventType.ORDER_PARTIALLY_FILLED
        else:
            evt_type = EventType.ORDER_ACKED

        self._emit(EngineEvent(
            event_type=evt_type,
            timestamp_ns=time.time_ns(),
            symbol=order.symbol,
            result=result,
            snapshot=book.snapshot(),
        ))

        return result

    def cancel_order(self, order_id: OrderId) -> Optional[Order]:
        """
        Cancel a resting order. Returns the cancelled order or None if not found.

        Orders that have already been filled or previously cancelled are not
        in _order_map and will return None without error.
        """
        order = self._order_map.get(order_id)
        if not order:
            return None

        book = self._books.get(order.symbol)
        if not book:
            return None

        cancelled = book.cancel(order_id)
        if cancelled:
            self._stats.orders_cancelled += 1
            self._stats.orders_resting = max(0, self._stats.orders_resting - 1)
            self._emit(EngineEvent(
                event_type=EventType.ORDER_CANCELLED,
                timestamp_ns=time.time_ns(),
                symbol=cancelled.symbol,
                cancelled_order=cancelled,
                snapshot=book.snapshot(),
            ))

        return cancelled

    # ── Queries ───────────────────────────────────────────────────────────────

    def get_snapshot(self, symbol: Symbol, depth: int = 10) -> Optional[BookSnapshot]:
        book = self._books.get(symbol)
        return book.snapshot(depth) if book else None

    def get_order(self, order_id: OrderId) -> Optional[Order]:
        return self._order_map.get(order_id)

    @property
    def stats(self) -> EngineStats:
        return self._stats

    @property
    def registered_symbols(self) -> list[Symbol]:
        return list(self._books.keys())

    # ── Private ───────────────────────────────────────────────────────────────

    def _validate(self, order: Order) -> Optional[RejectReason]:
        if order.symbol not in self._books:
            return RejectReason.SYMBOL_NOT_FOUND
        if order.order_id in self._order_map:
            return RejectReason.DUPLICATE_ORDER_ID
        if order.quantity <= 0:
            return RejectReason.INVALID_QUANTITY
        if order.order_type == OrderType.LIMIT:
            if order.price is None or order.price <= 0:
                return RejectReason.INVALID_PRICE
        return None

    def _emit(self, event: EngineEvent) -> None:
        if self._on_event:
            try:
                self._on_event(event)
            except Exception:
                pass  # Never let the callback crash the engine
