"""
Software implementation of MatchingHAL.

Wraps the Python CLOB MatchingEngine. This is the current production
implementation. It exists to:

1. Establish the HAL contract in working code (not just as an abstraction).
2. Provide a performance baseline for benchmarking C++ and FPGA backends.
3. Be a drop-in replacement for any code that previously held a direct
   MatchingEngine reference.

Performance profile (developer laptop, Python 3.11):
  Resting order (no fill):   ~350 µs  (dominated by Python allocation)
  1-fill match:              ~900 µs
  p99 processing latency:    ~1000 µs

When the C++ HAL (M7) lands, run the same benchmark suite against it.
Expect 10-100x improvement on processing latency alone.

Usage:
    from engine.hal.software import SoftwareMatchingHAL
    hal = SoftwareMatchingHAL()
    hal.register_symbol("AAPL")
    result = hal.submit_order(order)
"""
from __future__ import annotations
from typing import Callable, Optional

from engine.hal.abstract import MatchingHAL, EventCallback
from engine.core.matching_engine import MatchingEngine, EngineEvent
from engine.core.order import Order
from engine.core.order_book import MatchResult, BookSnapshot
from engine.core.types import OrderId, Symbol


class SoftwareMatchingHAL(MatchingHAL):
    """
    HAL implementation backed by the Python CLOB matching engine.

    The engine instance is owned by the HAL — one HAL = one engine.
    In a sharded deployment (one engine per instrument group), create
    one HAL per shard and route orders by symbol.
    """

    def __init__(self, on_event: Optional[EventCallback] = None) -> None:
        self._engine = MatchingEngine(on_event=on_event)

    # ── MatchingHAL implementation ────────────────────────────────────────────

    def submit_order(self, order: Order) -> MatchResult:
        return self._engine.submit_order(order)

    def cancel_order(self, order_id: OrderId) -> Optional[Order]:
        return self._engine.cancel_order(order_id)

    def get_snapshot(self, symbol: Symbol, depth: int = 10) -> Optional[BookSnapshot]:
        return self._engine.get_snapshot(symbol, depth)

    def get_order(self, order_id: OrderId) -> Optional[Order]:
        return self._engine.get_order(order_id)

    def register_symbol(self, symbol: Symbol) -> None:
        self._engine.register_symbol(symbol)

    def set_event_callback(self, cb: EventCallback) -> None:
        self._engine.set_event_callback(cb)

    @property
    def registered_symbols(self) -> list[Symbol]:
        return self._engine.registered_symbols

    # ── Pass-through to engine stats ──────────────────────────────────────────

    @property
    def stats(self):
        """Expose EngineStats for metrics endpoints."""
        return self._engine.stats

    # ── Health ────────────────────────────────────────────────────────────────

    def health_check(self) -> dict:
        stats = self._engine.stats
        return {
            "backend": self.backend_name,
            "symbols": self.registered_symbols,
            "orders_received": stats.orders_received,
            "trades_executed": stats.trades_executed,
            "p50_us": stats.p50_us,
            "p99_us": stats.p99_us,
        }
