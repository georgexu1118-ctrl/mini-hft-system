"""
Structural interface protocols for all major engine components.

Why Protocol instead of ABC?
─────────────────────────────
`typing.Protocol` uses structural subtyping (duck typing): a class satisfies
the protocol without explicitly inheriting from it. This matters because:

1. Native bindings are adapted behind Python HALs rather than exposed
   directly to service-layer dependencies.
2. The FPGA HAL will be a thin Python shim over a shared-memory interface.
   It has no natural base class.
3. Protocol lets us type-check without coupling the implementation to the
   interface definition file.

Runtime checkable (`@runtime_checkable`) means `isinstance(obj, MatchingBackend)`
works at runtime — useful for health checks and dynamic dispatch.

IMPORTANT: These protocols define the contract the HAL must satisfy.
           The service layer imports these, not concrete implementations.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from engine.core.order import Order
    from engine.core.order_book import BookSnapshot, MatchResult
    from engine.core.types import OrderId, Symbol
    from engine.risk.limits import RiskCheckResult


@runtime_checkable
class MatchingBackend(Protocol):
    """
    The minimal interface a matching backend must expose.

    Implementations:
    ─────────────────
    SoftwareMatchingHAL  (current)  Python CLOB, ~350 µs/order
    CppMatchingHAL       (M7)       C++ extension via binary pybind11 boundary
    FPGAMatchingHAL      (M8)       PCIe DMA shim, ~200 ns

    All implementations are drop-in replacements. The service layer
    depends only on this protocol.
    """
    def submit_order(self, order: "Order") -> "MatchResult": ...
    def cancel_order(self, order_id: "OrderId") -> "Order | None": ...
    def get_snapshot(self, symbol: "Symbol", depth: int = 10) -> "BookSnapshot | None": ...
    def get_order(self, order_id: "OrderId") -> "Order | None": ...
    def register_symbol(self, symbol: "Symbol") -> None: ...

    @property
    def registered_symbols(self) -> "list[Symbol]": ...


@runtime_checkable
class FeedHandler(Protocol):
    """
    Abstraction over the market data source.

    Current:   GBM synthetic feed (engine/market_data/publisher.py)
    Near-term: Replay of historical tick data (CSV/Parquet)
    Future:    Real UDP multicast binary feed (ITCH 5.0, MDP 3.0)
    FPGA:      Hardware packet parser → zero-copy shared memory

    The feed handler is the first place FPGA acceleration pays off.
    Parsing raw UDP packets (e.g. 100k messages/sec on CME) is a
    natural fit for FPGA because:
    - It's bandwidth-limited, not compute-limited
    - Packet fields map directly to hardware registers
    - The parse loop is perfectly regular (no branching on content)
    """
    async def run(self) -> None: ...
    def get_current_price(self, symbol: "Symbol") -> "float | None": ...

    @property
    def is_running(self) -> bool: ...


@runtime_checkable
class RiskBackend(Protocol):
    """
    Pre-trade risk check interface.

    Current:   Software comparisons in engine/risk/limits.py
    FPGA:      Single-cycle comparators against limit registers
               Latency: ~1 clock cycle (4 ns at 250 MHz) vs ~500 ns software

    In production, risk checks run on a *separate* FPGA function block
    that receives the order before it reaches the matching engine. A risk
    rejection never enters the order book pipeline.
    """
    def check(self, order: "Order") -> "RiskCheckResult": ...


@runtime_checkable
class EventSink(Protocol):
    """
    Abstraction over the post-match event delivery system.

    Current:   asyncio.Queue-based EventBus (shared/events.py)
    Future:    Lock-free ring buffer (engine/core/ring_buffer.py)
    FPGA path: Completion ring written by FPGA, polled by CPU

    EventSink decouples the engine callback from the specific queue type.
    The engine callback calls `publish_nowait(event)` — it doesn't know
    whether the sink is asyncio, a ring buffer, or a DMA completion queue.
    """
    def publish_nowait(self, event: object) -> None: ...
