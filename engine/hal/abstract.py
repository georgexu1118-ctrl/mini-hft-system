"""
Hardware Abstraction Layer (HAL) — the primary seam for FPGA/C++ integration.

The HAL sits between the service layer and the matching implementation.
It is the only boundary that changes when the backend changes.

Software path:
  OrderService → SoftwareMatchingHAL → MatchingEngine (Python CLOB)

C++ path (M7):
  OrderService → CppMatchingHAL → ctypes wrapper → compiled .so

FPGA path (M8):
  OrderService → FPGAMatchingHAL → DMA ring write → poll completion ring

In all three cases, `OrderService` calls the same methods on the same
abstract interface. The service layer has zero knowledge of what's below.

Why ABC instead of Protocol here?
───────────────────────────────────
The HAL is an internal architectural boundary, not an external interface.
ABC enforces that subclasses implement all methods at class definition time
rather than at call time. This gives us earlier error detection when someone
adds a new HAL method and forgets to implement it in a subclass.

`MatchingBackend` (protocols.py) uses Protocol for duck-typed external code.
`MatchingHAL` (this file) uses ABC for our own implementations.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Callable, Optional

from engine.core.order import Order
from engine.core.order_book import MatchResult, BookSnapshot
from engine.core.matching_engine import EngineEvent
from engine.core.types import OrderId, Symbol

# Type alias for the event callback installed by OrderService
EventCallback = Callable[[EngineEvent], None]


class MatchingHAL(ABC):
    """
    Abstract Hardware Abstraction Layer for the matching engine.

    Concrete implementations must override all abstract methods.
    The binary interface methods have default implementations that
    delegate to the Python object methods — FPGA subclasses override
    those to bypass Python object construction entirely.
    """

    # ── Primary Python-object interface (required) ────────────────────────────

    @abstractmethod
    def submit_order(self, order: Order) -> MatchResult:
        """
        Submit an Order object. Returns a MatchResult object.

        Software HAL: calls MatchingEngine.submit_order(order) directly.
        FPGA HAL:     encodes order to bytes, writes to DMA ring,
                      polls completion ring, decodes result bytes.
        """
        ...

    @abstractmethod
    def cancel_order(self, order_id: OrderId) -> Optional[Order]:
        """Cancel a resting order. Returns the cancelled Order or None."""
        ...

    @abstractmethod
    def get_snapshot(self, symbol: Symbol, depth: int = 10) -> Optional[BookSnapshot]:
        """Return a point-in-time depth snapshot of the order book."""
        ...

    @abstractmethod
    def get_order(self, order_id: OrderId) -> Optional[Order]:
        """
        Retrieve an order by ID.

        Note for FPGA HAL: the FPGA doesn't maintain an order history,
        only the current book state. The FPGA HAL must maintain a
        host-side order cache populated from the event stream.
        """
        ...

    @abstractmethod
    def register_symbol(self, symbol: Symbol) -> None:
        """Register a tradeable instrument before the first order."""
        ...

    @abstractmethod
    def set_event_callback(self, cb: EventCallback) -> None:
        """
        Install the callback invoked after every order operation.

        IMPORTANT: The callback MUST be synchronous and non-blocking.
        It is called inside the matching critical section.

        Software HAL: passes directly to MatchingEngine.set_event_callback.
        FPGA HAL:     called from the completion ring polling loop.
        """
        ...

    @property
    @abstractmethod
    def registered_symbols(self) -> list[Symbol]:
        ...

    # ── Binary interface (optional FPGA fast path) ────────────────────────────

    def submit_order_bytes(self, frame: bytes) -> bytes:
        """
        Binary fast path: encode → submit → encode result.

        Default implementation: decode frame → call submit_order → encode result.
        This costs two codec operations but requires no changes to OrderService.

        FPGA HAL override: write frame directly to DMA ring, poll completion.
        Eliminates Python object construction on the hot path entirely.

        Expected latency:
          Default (Python objects):  same as submit_order  (~350 µs)
          FPGA override:             DMA write + poll       (~200 ns)
        """
        from engine.core.binary_protocol import OrderCodec, MatchResultCodec
        order = OrderCodec.decode(frame)
        result = self.submit_order(order)
        return MatchResultCodec.encode(result)

    def cancel_order_bytes(self, order_id_bytes: bytes) -> bytes:
        """
        Binary fast path for cancel.
        Default: decode UUID, cancel, encode result (cancelled or empty).
        FPGA override: write cancel frame to DMA command ring.
        """
        import uuid
        order_id = str(uuid.UUID(bytes=order_id_bytes[:16]))
        order = self.cancel_order(order_id)
        if order is None:
            return b"\x00" * 64
        from engine.core.binary_protocol import OrderCodec
        return OrderCodec.encode(order)

    # ── Metrics ───────────────────────────────────────────────────────────────

    @property
    def backend_name(self) -> str:
        """Human-readable identifier for the active backend."""
        return self.__class__.__name__

    def health_check(self) -> dict:
        """
        Return a dict describing backend health.

        Used by the /health endpoint to surface backend type and status.
        FPGA HAL adds: PCIe link status, DMA queue depths, FPGA temperature.
        """
        return {
            "backend": self.backend_name,
            "symbols": self.registered_symbols,
        }
