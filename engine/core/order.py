from __future__ import annotations
import uuid
import time
from dataclasses import dataclass, field
from typing import Optional

from .types import (
    OrderSide, OrderType, OrderStatus, TimeInForce,
    OrderId, Symbol, Price, Quantity, TimestampNs,
)


@dataclass(slots=True)
class Order:
    """
    Canonical order representation used throughout the system.

    slots=True eliminates per-instance __dict__ overhead — meaningful
    when allocating thousands of orders per second. In C++ this maps to
    a fixed-size struct allocated from a memory pool. In FPGA, these
    fields live in registers or BRAM.

    Timestamps use nanoseconds (time_ns) for latency profiling. Multiple
    checkpoints across the lifecycle let us isolate exactly where latency
    accumulates: network → gateway → queue → matching → ack.
    """
    symbol: Symbol
    side: OrderSide
    order_type: OrderType
    quantity: Quantity

    # None for MARKET orders (price-insensitive, sweep the book)
    price: Optional[Price] = None
    time_in_force: TimeInForce = TimeInForce.GTC
    strategy_id: Optional[str] = None
    # Client-supplied tag; echoed back in acks for order reconciliation
    client_order_id: Optional[str] = None

    # Engine-assigned — not provided by the client
    order_id: OrderId = field(default_factory=lambda: str(uuid.uuid4()))
    status: OrderStatus = OrderStatus.PENDING
    filled_quantity: Quantity = 0
    avg_fill_price: Optional[float] = None

    # Latency profile checkpoints (nanoseconds since epoch)
    created_at_ns: TimestampNs = field(default_factory=time.time_ns)
    received_at_ns: TimestampNs = 0    # gateway ingress
    queued_at_ns: TimestampNs = 0      # engine queue entry
    processed_at_ns: TimestampNs = 0   # matching complete
    acked_at_ns: TimestampNs = 0       # ack dispatched to client

    # ── Binary codec (lazy import avoids circular dep) ────────────────────────

    def to_bytes(self) -> bytes:
        """Encode to 64-byte cache-line-aligned frame for DMA/ring-buffer path."""
        from engine.core.binary_protocol import OrderCodec
        return OrderCodec.encode(self)

    def encode_into(self, buf: bytearray, offset: int = 0) -> None:
        """Zero-copy encode directly into a pre-allocated bytearray."""
        from engine.core.binary_protocol import OrderCodec
        OrderCodec.encode_into(self, buf, offset)

    @classmethod
    def from_bytes(cls, data: bytes | bytearray, offset: int = 0) -> "Order":
        """Decode from a 64-byte frame (e.g. from SPSC ring buffer slot)."""
        from engine.core.binary_protocol import OrderCodec
        return OrderCodec.decode(data, offset)

    @property
    def remaining_quantity(self) -> Quantity:
        return self.quantity - self.filled_quantity

    @property
    def is_buy(self) -> bool:
        return self.side == OrderSide.BUY

    @property
    def is_sell(self) -> bool:
        return self.side == OrderSide.SELL

    @property
    def is_market(self) -> bool:
        return self.order_type == OrderType.MARKET

    @property
    def is_limit(self) -> bool:
        return self.order_type == OrderType.LIMIT

    @property
    def is_active(self) -> bool:
        return self.status in (OrderStatus.OPEN, OrderStatus.PARTIALLY_FILLED)

    @property
    def gateway_latency_us(self) -> Optional[float]:
        """Network + serialization cost: creation → gateway receipt."""
        if self.received_at_ns and self.created_at_ns:
            return (self.received_at_ns - self.created_at_ns) / 1_000
        return None

    @property
    def processing_latency_us(self) -> Optional[float]:
        """Pure engine cost: queue entry → match complete. The number we optimize."""
        if self.processed_at_ns and self.received_at_ns:
            return (self.processed_at_ns - self.received_at_ns) / 1_000
        return None

    @property
    def roundtrip_latency_us(self) -> Optional[float]:
        """End-to-end: creation → ack received."""
        if self.acked_at_ns and self.created_at_ns:
            return (self.acked_at_ns - self.created_at_ns) / 1_000
        return None


@dataclass(slots=True)
class Trade:
    """
    Immutable execution record produced by the matching engine.

    Always written by the engine; never mutated after creation.
    In production: append-only trade log, replicated to time-series DB
    and regulatory reporting system. The aggressor/maker distinction
    matters for exchange fee tiers (makers pay less or earn rebates).
    """
    symbol: Symbol
    price: Price
    quantity: Quantity
    # Aggressor = taker = incoming order that crossed the spread
    aggressor_side: OrderSide
    # Maker = resting order that was sitting in the book
    maker_order_id: OrderId
    # Taker = the new order that triggered this trade
    taker_order_id: OrderId

    trade_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp_ns: TimestampNs = field(default_factory=time.time_ns)

    @property
    def notional(self) -> float:
        """Dollar value of the trade."""
        return self.price * self.quantity
