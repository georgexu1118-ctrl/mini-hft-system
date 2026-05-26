"""
MatchingHAL implementation backed by the C++20 matching core.

The extension boundary is fixed-size binary data, not Python domain objects:
`ORDER_FRAME` is a stable, big-endian transport ABI suitable for a future
shared-memory or DMA gateway. Native C++ structs remain host-endian internal
state and are never reinterpret-cast from transport buffers.

The public object API is the service-layer warm path. It reconstructs events,
snapshots, and cache objects after native matching so existing API, strategy,
and persistence components remain independent of backend selection.
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Optional

from engine.core.binary_protocol import BookSnapshotCodec
from engine.core.matching_engine import EngineEvent, EngineStats
from engine.core.order import Order, Trade
from engine.core.order_book import BookSnapshot, MatchResult
from engine.core.types import EventType, OrderId, OrderSide, RejectReason, Symbol
from engine.hal.abstract import EventCallback, MatchingHAL

try:
    import hft_engine_cpp as _cpp  # type: ignore[import-not-found]

    _AVAILABLE = True
except ImportError:
    _cpp = None
    _AVAILABLE = False


def is_available() -> bool:
    """Return whether the locally built pybind11 extension is importable."""
    return _AVAILABLE


_REJECTIONS: dict[int, RejectReason] = {
    1: RejectReason.SYMBOL_NOT_FOUND,
    2: RejectReason.DUPLICATE_ORDER_ID,
    3: RejectReason.INVALID_QUANTITY,
    4: RejectReason.INVALID_PRICE,
}


class CppMatchingHAL(MatchingHAL):
    """
    Service-compatible HAL above the synchronous C++ matching facade.

    One order currently incurs one stable native node allocation. This is a
    measured baseline, not a claim of zero allocation; a slab allocator can be
    introduced after benchmark data shows native allocation is material.
    """

    def __init__(self, on_event: Optional[EventCallback] = None) -> None:
        if not _AVAILABLE:
            raise ImportError(
                "C++ engine extension (hft_engine_cpp) is not built. "
                "Run: python engine/cpp/setup.py build_ext --inplace"
            )
        self._cpp_engine = _cpp.CppEngine()
        self._event_callback = on_event
        self._symbols: list[Symbol] = []
        self._orders: dict[OrderId, Order] = {}
        self._stats = EngineStats()

    @property
    def backend_name(self) -> str:
        return "cpp"

    def register_symbol(self, symbol: Symbol) -> None:
        if not symbol.isascii() or not 1 <= len(symbol.encode("ascii")) <= 6:
            raise ValueError("C++ ORDER_FRAME symbol must contain 1 to 6 ASCII bytes")
        self._cpp_engine.register_symbol(symbol)
        if symbol not in self._symbols:
            self._symbols.append(symbol)

    def submit_order(self, order: Order) -> MatchResult:
        """
        Match synchronously in C++ and project native results into domain state.

        Complexity is dominated by the native O(log P + F) matching operation,
        plus bounded frame conversion and F Python Trade objects required by
        the existing downstream event contract.
        """
        order.received_at_ns = time.time_ns()
        detail: dict[str, Any] = self._cpp_engine.submit_order_detail(order.to_bytes())
        native_order = Order.from_bytes(detail["order_frame"])
        self._copy_native_state(native_order, order)
        rejection = _REJECTIONS.get(int(detail["rejection"]))

        trades = [self._trade_from_tuple(order.symbol, values) for values in detail["trades"]]
        result = MatchResult(
            order=order,
            trades=trades,
            is_new_resting=bool(detail["is_new_resting"]),
        )
        if trades:
            order.avg_fill_price = result.avg_fill_price

        self._copy_native_counters()
        if rejection is None:
            self._orders[order.order_id] = order
            for trade in trades:
                self._refresh_cached_order(trade.maker_order_id)
            if order.processing_latency_us is not None:
                self._stats.record_latency(order.processing_latency_us)
            order.acked_at_ns = time.time_ns()

        if rejection is not None:
            event_type = EventType.ORDER_REJECTED
        elif trades and order.remaining_quantity == 0:
            event_type = EventType.ORDER_FILLED
        elif trades:
            event_type = EventType.ORDER_PARTIALLY_FILLED
        else:
            event_type = EventType.ORDER_ACKED

        snapshot_frame = detail["snapshot_frame"]
        snapshot = None if snapshot_frame is None else BookSnapshotCodec.decode(snapshot_frame)
        self._emit(EngineEvent(
            event_type=event_type,
            timestamp_ns=time.time_ns(),
            symbol=order.symbol,
            result=result,
            reject_reason=rejection,
            snapshot=snapshot,
        ))
        return result

    def cancel_order(self, order_id: OrderId) -> Optional[Order]:
        identifier = self._identifier_bytes(order_id)
        if identifier is None:
            return None
        raw = self._cpp_engine.cancel_order_bytes(identifier)
        if raw is None:
            return None
        native_order = Order.from_bytes(raw)
        order = self._orders.get(order_id, native_order)
        self._copy_native_state(native_order, order)
        self._copy_native_counters()
        snapshot = self.get_snapshot(order.symbol)
        self._emit(EngineEvent(
            event_type=EventType.ORDER_CANCELLED,
            timestamp_ns=time.time_ns(),
            symbol=order.symbol,
            cancelled_order=order,
            snapshot=snapshot,
        ))
        return order

    def get_snapshot(self, symbol: Symbol, depth: int = 10) -> Optional[BookSnapshot]:
        raw = self._cpp_engine.get_snapshot_bytes(symbol, depth)
        return None if raw is None else BookSnapshotCodec.decode(raw)

    def get_order(self, order_id: OrderId) -> Optional[Order]:
        identifier = self._identifier_bytes(order_id)
        if identifier is None:
            return None
        raw = self._cpp_engine.get_order_bytes(identifier)
        if raw is None:
            return None
        native_order = Order.from_bytes(raw)
        order = self._orders.get(order_id, native_order)
        self._copy_native_state(native_order, order)
        return order

    def set_event_callback(self, cb: EventCallback) -> None:
        self._event_callback = cb

    @property
    def registered_symbols(self) -> list[Symbol]:
        return list(self._symbols)

    def submit_order_bytes(self, frame: bytes) -> bytes:
        """Submit an encoded order without Python domain reconstruction."""
        return self._cpp_engine.submit_order_bytes(frame)

    def cancel_order_bytes(self, order_id_bytes: bytes) -> bytes:
        raw = self._cpp_engine.cancel_order_bytes(order_id_bytes)
        return b"\x00" * 64 if raw is None else raw

    @property
    def stats(self) -> EngineStats:
        return self._stats

    def health_check(self) -> dict[str, object]:
        return {
            "backend": self.backend_name,
            "symbols": self.registered_symbols,
            "orders_received": self._stats.orders_received,
            "trades_executed": self._stats.trades_executed,
            "p50_us": self._stats.p50_us,
            "p99_us": self._stats.p99_us,
        }

    @staticmethod
    def _identifier_bytes(order_id: OrderId) -> Optional[bytes]:
        try:
            return uuid.UUID(order_id).bytes
        except ValueError:
            return None

    @staticmethod
    def _copy_native_state(native_order: Order, order: Order) -> None:
        order.status = native_order.status
        order.filled_quantity = native_order.filled_quantity
        order.processed_at_ns = native_order.processed_at_ns

    def _refresh_cached_order(self, order_id: OrderId) -> None:
        order = self._orders.get(order_id)
        identifier = self._identifier_bytes(order_id)
        if order is None or identifier is None:
            return
        raw = self._cpp_engine.get_order_bytes(identifier)
        if raw is not None:
            self._copy_native_state(Order.from_bytes(raw), order)

    @staticmethod
    def _trade_from_tuple(symbol: Symbol, values: tuple[Any, ...]) -> Trade:
        trade_id, price, quantity, aggressor, maker, taker, timestamp_ns = values
        return Trade(
            symbol=symbol,
            price=float(price),
            quantity=int(quantity),
            aggressor_side=OrderSide.BUY if int(aggressor) == 0 else OrderSide.SELL,
            maker_order_id=str(uuid.UUID(bytes=maker)),
            taker_order_id=str(uuid.UUID(bytes=taker)),
            trade_id=str(uuid.UUID(bytes=trade_id)),
            timestamp_ns=int(timestamp_ns),
        )

    def _copy_native_counters(self) -> None:
        native = self._cpp_engine.stats()
        self._stats.orders_received = int(native["orders_received"])
        self._stats.orders_matched = int(native["orders_matched"])
        self._stats.orders_resting = int(native["orders_resting"])
        self._stats.orders_cancelled = int(native["orders_cancelled"])
        self._stats.orders_rejected = int(native["orders_rejected"])
        self._stats.trades_executed = int(native["trades_executed"])
        self._stats.total_volume = int(native["total_volume"])

    def _emit(self, event: EngineEvent) -> None:
        if self._event_callback:
            try:
                self._event_callback(event)
            except Exception:
                pass
