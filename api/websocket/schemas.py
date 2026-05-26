"""
WebSocket message schemas (Pydantic v2).

All messages share a `type` discriminator field so clients can use a
single discriminated-union parser to handle every message type without
a big if-elif chain.

TypeScript equivalent (for the frontend):
    type WSMessage =
      | BookSnapshotMessage
      | BookDeltaMessage
      | TradeMessage
      | OrderAckMessage
      | TickerMessage
      | LatencySnapshotMessage
      | StrategyMetricsMessage
      | PnlMessage
      | SystemHealthMessage
      | HeartbeatMessage
      | ErrorMessage;

    // Discriminate on `msg.type`

Framing: each message is a single JSON text frame. Binary frames (e.g.
FlatBuffers, protobuf) would halve serialisation cost but complicate
browser clients. JSON is fine for our simulation scale.
"""
from __future__ import annotations

import time
from typing import Literal

from pydantic import BaseModel, Field

# ── Sub-objects ───────────────────────────────────────────────────────────────

class BookLevel(BaseModel):
    """Aggregate data at a single price level."""
    price: float
    quantity: int
    order_count: int


# ── Server → Client messages ──────────────────────────────────────────────────

class BookSnapshotMessage(BaseModel):
    """
    Recovery/bootstrap image of depth. Clients apply following deltas in order.
    """
    type: Literal["BOOK_SNAPSHOT"] = "BOOK_SNAPSHOT"
    symbol: str
    sequence: int                  # monotonically increasing per symbol
    bids: list[BookLevel]          # sorted descending (best bid first)
    asks: list[BookLevel]          # sorted ascending (best ask first)
    spread: float | None = None
    mid_price: float | None = None
    timestamp_ns: int = Field(default_factory=time.time_ns)


class BookDeltaMessage(BaseModel):
    """Changed levels only; quantity zero deletes a level from local state."""

    type: Literal["BOOK_DELTA"] = "BOOK_DELTA"
    symbol: str
    previous_sequence: int
    sequence: int
    bids: list[BookLevel]
    asks: list[BookLevel]
    spread: float | None = None
    mid_price: float | None = None
    timestamp_ns: int = Field(default_factory=time.time_ns)


# Retain the import surface for consumers built against the first API milestone.
BookUpdateMessage = BookSnapshotMessage


class TradeMessage(BaseModel):
    """Public trade print — sent to all subscribers when a match occurs."""
    type: Literal["TRADE"] = "TRADE"
    trade_id: str
    symbol: str
    price: float
    quantity: int
    notional: float                  # price × quantity
    aggressor_side: str              # "BUY" | "SELL"
    maker_order_id: str
    taker_order_id: str
    timestamp_ns: int


class OrderAckMessage(BaseModel):
    """
    Per-order status update sent to the originating client (and all subs).

    Covers: new resting order, partial fill, full fill, cancel.
    The status field tells the client which case this is.

    Latency fields are the educational core — the dashboard should render
    these prominently so users can observe the matching latency.
    """
    type: Literal["ORDER_ACK"] = "ORDER_ACK"
    order_id: str
    client_order_id: str | None = None
    symbol: str
    side: str
    status: str                       # OrderStatus value
    quantity: int
    filled_quantity: int
    remaining_quantity: int
    avg_fill_price: float | None = None
    trades_count: int = 0

    # Latency breakdown (all in microseconds)
    gateway_latency_us: float | None = None    # client → gateway
    processing_latency_us: float | None = None  # queue → match complete
    roundtrip_latency_us: float | None = None   # end-to-end
    timestamp_ns: int = Field(default_factory=time.time_ns)


class TickerMessage(BaseModel):
    """Synthetic best-bid/offer ticker from the market data feed."""
    type: Literal["TICKER"] = "TICKER"
    symbol: str
    bid: float
    ask: float
    last: float
    volume: int
    timestamp_ns: int


class LatencySnapshotMessage(BaseModel):
    """
    Periodic latency statistics pushed to all clients.

    These are the numbers that HFT engineers obsess over.
    p99 > p50 by 5-10× is normal; p999 spikes indicate GC pauses or
    OS scheduling jitter (in Python). A C++ engine would show <1µs p999.
    """
    type: Literal["LATENCY_SNAPSHOT"] = "LATENCY_SNAPSHOT"
    p50_us: float | None = None
    p99_us: float | None = None
    p999_us: float | None = None
    orders_received: int = 0
    trades_executed: int = 0
    total_volume: int = 0
    dropped_events: int = 0
    timestamp_ns: int = Field(default_factory=time.time_ns)


class StrategyMetricsMessage(BaseModel):
    type: Literal["STRATEGY_METRICS"] = "STRATEGY_METRICS"
    strategies: dict[str, dict[str, float | int | str | None]]
    timestamp_ns: int = Field(default_factory=time.time_ns)


class PnlMessage(BaseModel):
    type: Literal["PNL"] = "PNL"
    strategies: dict[str, dict[str, float | int | str | None]]
    timestamp_ns: int = Field(default_factory=time.time_ns)


class SystemHealthMessage(BaseModel):
    type: Literal["SYSTEM_HEALTH"] = "SYSTEM_HEALTH"
    status: Literal["OK", "DEGRADED"]
    event_bus_drops: int
    persistence_drops: int
    persistence_failed_batches: int
    strategy_callback_failures: int
    timestamp_ns: int = Field(default_factory=time.time_ns)


class HeartbeatMessage(BaseModel):
    """Keep-alive ping. Client should disconnect if heartbeats stop."""
    type: Literal["HEARTBEAT"] = "HEARTBEAT"
    timestamp_ns: int = Field(default_factory=time.time_ns)


class ErrorMessage(BaseModel):
    """Server-side error surfaced to the client."""
    type: Literal["ERROR"] = "ERROR"
    code: str
    message: str
    timestamp_ns: int = Field(default_factory=time.time_ns)


# ── Client → Server messages ──────────────────────────────────────────────────

class SubscribeRequest(BaseModel):
    """Client sends this to subscribe/unsubscribe from a symbol feed."""
    action: Literal["subscribe", "unsubscribe", "subscribe_all"]
    symbol: str | None = None   # None with subscribe_all → receive everything
