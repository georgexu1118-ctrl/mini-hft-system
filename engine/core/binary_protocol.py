"""
Fixed-size binary message protocol for DMA and FPGA-compatible messaging.

Design principles
──────────────────
1. Fixed-size frames — every message type has a compile-time-constant byte size.
   Variable-length data is not DMA-friendly and cannot be pre-allocated.

2. Cache-line aligned (64 bytes) — a struct that fits in one cache line is
   fetched in a single L1 cache miss. Structs that straddle cache lines cause
   two misses. At ~4 ns/miss this is measurable at µs latency scales.

3. Big-endian (network byte order) — standard for network protocols and
   FPGA hardware. The FPGA always sees the same byte order regardless of
   the host CPU's endianness.

4. No pointers, no strings, no Python objects — only scalars and fixed arrays.
   This makes the binary format trivially mmap-able and DMA-able.

5. Reserved padding — future fields don't break the struct size.

Wire format reference (all sizes in bytes)
───────────────────────────────────────────

ORDER_FRAME (64 bytes = 1 cache line):
  ┌──────┬──────┬────────────────┬─────────┐
  │  Off │ Size │ Field          │ Type    │
  ├──────┼──────┼────────────────┼─────────┤
  │    0 │   16 │ order_id       │ bytes   │ UUID as raw 16-byte sequence
  │   16 │    8 │ price          │ float64 │ IEEE 754 double, 0.0 for MARKET
  │   24 │    4 │ quantity       │ uint32  │
  │   28 │    4 │ filled_qty     │ uint32  │
  │   32 │    8 │ created_at_ns  │ uint64  │ nanoseconds since epoch
  │   40 │    8 │ processed_ns   │ uint64  │ 0 until engine sets it
  │   48 │    6 │ symbol         │ char[6] │ ASCII, right-padded with 0x00
  │   54 │    1 │ side           │ uint8   │ 0=BUY 1=SELL
  │   55 │    1 │ order_type     │ uint8   │ 0=LIMIT 1=MARKET
  │   56 │    1 │ time_in_force  │ uint8   │ 0=GTC 1=IOC 2=FOK 3=GTD
  │   57 │    1 │ status         │ uint8   │ see OrderStatus enum
  │   58 │    6 │ reserved       │ padding │ must be 0x00
  └──────┴──────┴────────────────┴─────────┘

TRADE_FRAME (64 bytes = 1 cache line):
  ┌──────┬──────┬────────────────┬─────────┐
  │    0 │   16 │ trade_id       │ bytes   │ UUID raw bytes
  │   16 │    8 │ price          │ float64 │
  │   24 │    4 │ quantity       │ uint32  │
  │   28 │    1 │ aggressor_side │ uint8   │ 0=BUY 1=SELL
  │   29 │    3 │ reserved       │ padding │
  │   32 │    8 │ timestamp_ns   │ uint64  │
  │   40 │    8 │ sequence       │ uint64  │ monotonic trade sequence
  │   48 │    8 │ maker_id_hi    │ uint64  │ maker order UUID high 8 bytes
  │   56 │    8 │ maker_id_lo    │ uint64  │ maker order UUID low 8 bytes
  └──────┴──────┴────────────────┴─────────┘

BOOK_LEVEL_FRAME (16 bytes):
  ┌──────┬──────┬────────────────┬─────────┐
  │    0 │    8 │ price          │ float64 │
  │    8 │    4 │ quantity       │ uint32  │
  │   12 │    4 │ order_count    │ uint32  │
  └──────┴──────┴────────────────┴─────────┘

SNAPSHOT_HEADER (32 bytes) + N × BOOK_LEVEL_FRAME:
  ┌──────┬──────┬────────────────┬─────────┐
  │    0 │    6 │ symbol         │ char[6] │
  │    6 │    2 │ bid_count      │ uint16  │ levels included
  │    8 │    2 │ ask_count      │ uint16  │
  │   10 │    2 │ reserved       │ padding │
  │   12 │    4 │ sequence       │ uint32  │
  │   16 │    8 │ timestamp_ns   │ uint64  │
  │   24 │    8 │ reserved       │ padding │
  └──────┴──────┴────────────────┴─────────┘

PCIe DMA usage (M8 FPGA integration)
──────────────────────────────────────
The host allocates a hugepage region (2 MB or 1 GB) and maps it as a
write-combine memory region. The FPGA is given the physical address via
the device driver. DMA writes arrive directly into cache without CPU
involvement. The CPU polls a completion counter (also in the hugepage)
to know when a new result frame is ready.

  Host                                FPGA
  ────                                ────
  alloc_hugepage() → phys_addr        order_queue: BRAM ring, written by DMA
  mmap(phys_addr)  → virt_addr        result_queue: writes to host hugepage
  write ORDER_FRAME to ring[head]     reads ring[tail], matches, writes TRADE_FRAME
  head += 1                           result_head += 1
  poll result_head != result_tail     (completion signal)
  read TRADE_FRAME from result_ring
"""
from __future__ import annotations

import struct
import uuid
from typing import TYPE_CHECKING

from engine.core.order import Order, Trade
from engine.core.types import OrderSide, OrderStatus, OrderType, TimeInForce

if TYPE_CHECKING:
    from engine.core.order_book import BookSnapshot, MatchResult


# ── Enum → wire byte mappings ─────────────────────────────────────────────────
# Keep these in sync with the FPGA HDL parameter definitions.

_SIDE_TO_WIRE: dict[OrderSide, int] = {
    OrderSide.BUY: 0,
    OrderSide.SELL: 1,
}
_WIRE_TO_SIDE: dict[int, OrderSide] = {v: k for k, v in _SIDE_TO_WIRE.items()}

_TYPE_TO_WIRE: dict[OrderType, int] = {
    OrderType.LIMIT: 0,
    OrderType.MARKET: 1,
}
_WIRE_TO_TYPE: dict[int, OrderType] = {v: k for k, v in _TYPE_TO_WIRE.items()}

_TIF_TO_WIRE: dict[TimeInForce, int] = {
    TimeInForce.GTC: 0,
    TimeInForce.IOC: 1,
    TimeInForce.FOK: 2,
    TimeInForce.GTD: 3,
}
_WIRE_TO_TIF: dict[int, TimeInForce] = {v: k for k, v in _TIF_TO_WIRE.items()}

_STATUS_TO_WIRE: dict[OrderStatus, int] = {
    OrderStatus.PENDING: 0,
    OrderStatus.OPEN: 1,
    OrderStatus.PARTIALLY_FILLED: 2,
    OrderStatus.FILLED: 3,
    OrderStatus.CANCELLED: 4,
    OrderStatus.REJECTED: 5,
}
_WIRE_TO_STATUS: dict[int, OrderStatus] = {v: k for k, v in _STATUS_TO_WIRE.items()}


# ── Struct definitions ────────────────────────────────────────────────────────

# Big-endian, 64-byte order frame
# Format: order_id(16) price(8) qty(4) filled(4) created_ns(8) processed_ns(8)
#         symbol(6) side(1) type(1) tif(1) status(1) reserved(6)
ORDER_FRAME = struct.Struct(">16s d I I Q Q 6s B B B B 6x")
assert ORDER_FRAME.size == 64, f"ORDER_FRAME must be 64 bytes, got {ORDER_FRAME.size}"

# Big-endian, 64-byte trade frame
# trade_id(16) price(8) qty(4) side(1) pad(3) ts_ns(8) seq(8) maker_hi(8) maker_lo(8)
TRADE_FRAME = struct.Struct(">16s d I B 3x Q Q 8s 8s")
assert TRADE_FRAME.size == 64, f"TRADE_FRAME must be 64 bytes, got {TRADE_FRAME.size}"

# 16-byte book level frame
BOOK_LEVEL_FRAME = struct.Struct(">d I I")
assert BOOK_LEVEL_FRAME.size == 16

# 32-byte snapshot header
SNAPSHOT_HEADER = struct.Struct(">6s H H 2x I Q 8x")
assert SNAPSHOT_HEADER.size == 32


def _encode_symbol(symbol: str) -> bytes:
    """Encode symbol string to 6-byte null-padded ASCII."""
    b = symbol.encode("ascii")[:6]
    return b.ljust(6, b"\x00")


def _decode_symbol(raw: bytes) -> str:
    """Decode 6-byte null-padded ASCII to symbol string."""
    return raw.rstrip(b"\x00").decode("ascii")


def _encode_uuid(uid: str) -> bytes:
    """Encode UUID string to 16-byte raw bytes."""
    return uuid.UUID(uid).bytes


def _decode_uuid(raw: bytes) -> str:
    """Decode 16-byte raw bytes to UUID string."""
    return str(uuid.UUID(bytes=raw))


# ── OrderCodec ────────────────────────────────────────────────────────────────

class OrderCodec:
    """
    Encode/decode Order objects to/from the 64-byte ORDER_FRAME wire format.

    Usage (hot path — pre-allocate a bytearray and reuse it):
        buf = bytearray(64)
        OrderCodec.encode_into(order, buf)  # zero-copy variant
        # ... write buf to DMA ring ...

    Usage (convenience):
        data = OrderCodec.encode(order)
        order = OrderCodec.decode(data)
    """

    @staticmethod
    def encode(order: Order) -> bytes:
        """Encode Order to 64-byte big-endian frame."""
        return ORDER_FRAME.pack(
            _encode_uuid(order.order_id),
            order.price if order.price is not None else 0.0,
            order.quantity,
            order.filled_quantity,
            order.created_at_ns,
            order.processed_at_ns,
            _encode_symbol(order.symbol),
            _SIDE_TO_WIRE[order.side],
            _TYPE_TO_WIRE[order.order_type],
            _TIF_TO_WIRE[order.time_in_force],
            _STATUS_TO_WIRE[order.status],
        )

    @staticmethod
    def encode_into(order: Order, buf: bytearray, offset: int = 0) -> None:
        """
        Zero-allocation encode: pack directly into a pre-allocated buffer.

        Use this on the hot path. Avoids creating a new `bytes` object.
        With FPGA DMA, `buf` is the hugepage-mapped ring buffer; this call
        writes the frame in-place and the DMA engine reads it directly.
        """
        ORDER_FRAME.pack_into(
            buf, offset,
            _encode_uuid(order.order_id),
            order.price if order.price is not None else 0.0,
            order.quantity,
            order.filled_quantity,
            order.created_at_ns,
            order.processed_at_ns,
            _encode_symbol(order.symbol),
            _SIDE_TO_WIRE[order.side],
            _TYPE_TO_WIRE[order.order_type],
            _TIF_TO_WIRE[order.time_in_force],
            _STATUS_TO_WIRE[order.status],
        )

    @staticmethod
    def decode(data: bytes | bytearray, offset: int = 0) -> Order:
        """Decode a 64-byte frame back to an Order object."""
        (
            raw_id, price, qty, filled, created_ns, processed_ns,
            raw_symbol, side_b, type_b, tif_b, status_b,
        ) = ORDER_FRAME.unpack_from(data, offset)

        order = Order(
            symbol=_decode_symbol(raw_symbol),
            side=_WIRE_TO_SIDE[side_b],
            order_type=_WIRE_TO_TYPE[type_b],
            quantity=qty,
            price=price if price != 0.0 else None,
            time_in_force=_WIRE_TO_TIF[tif_b],
        )
        object.__setattr__(order, "order_id", _decode_uuid(raw_id))
        object.__setattr__(order, "filled_quantity", filled)
        object.__setattr__(order, "created_at_ns", created_ns)
        object.__setattr__(order, "processed_at_ns", processed_ns)
        object.__setattr__(order, "status", _WIRE_TO_STATUS[status_b])
        return order


# ── TradeCodec ────────────────────────────────────────────────────────────────

class TradeCodec:
    """Encode/decode Trade objects to/from the 64-byte TRADE_FRAME wire format."""

    @staticmethod
    def encode(trade: Trade) -> bytes:
        maker_bytes = _encode_uuid(trade.maker_order_id)
        return TRADE_FRAME.pack(
            _encode_uuid(trade.trade_id),
            trade.price,
            trade.quantity,
            _SIDE_TO_WIRE[trade.aggressor_side],
            trade.timestamp_ns,
            0,                     # sequence (populated by engine counter)
            maker_bytes[:8],       # maker UUID high 8 bytes
            maker_bytes[8:],       # maker UUID low 8 bytes
        )

    @staticmethod
    def decode(data: bytes | bytearray, offset: int = 0) -> dict:
        """
        Partial decode: returns a dict of scalars.
        Full Trade reconstruction requires maker/taker order IDs from
        a side-channel (order map), since the frame only stores maker.
        """
        (
            raw_trade_id, price, qty, side_b, ts_ns, seq,
            maker_hi, maker_lo,
        ) = TRADE_FRAME.unpack_from(data, offset)

        maker_bytes = maker_hi + maker_lo
        return {
            "trade_id": _decode_uuid(raw_trade_id),
            "price": price,
            "quantity": qty,
            "aggressor_side": _WIRE_TO_SIDE[side_b],
            "timestamp_ns": ts_ns,
            "sequence": seq,
            "maker_order_id": _decode_uuid(maker_bytes),
        }


# ── BookLevelCodec ────────────────────────────────────────────────────────────

class BookLevelCodec:
    """Encode/decode a single order book price level (16 bytes)."""

    @staticmethod
    def encode(price: float, quantity: int, order_count: int) -> bytes:
        return BOOK_LEVEL_FRAME.pack(price, quantity, order_count)

    @staticmethod
    def decode(data: bytes | bytearray, offset: int = 0) -> tuple[float, int, int]:
        return BOOK_LEVEL_FRAME.unpack_from(data, offset)


# ── BookSnapshotCodec ─────────────────────────────────────────────────────────

class BookSnapshotCodec:
    """
    Encode/decode a book snapshot: 32-byte header + N×16-byte levels.

    Total size = 32 + (bid_count + ask_count) × 16 bytes.
    For depth=10: 32 + 20×16 = 352 bytes.

    This format is what the FPGA writes to the DMA completion ring after
    every book-changing event. The CPU polls the ring, reads the frame,
    and delivers it to the WS broadcaster without any Python allocation
    on the critical notification path.
    """

    @staticmethod
    def encode(snapshot: "BookSnapshot") -> bytes:
        bid_count = len(snapshot.bids)
        ask_count = len(snapshot.asks)

        header = SNAPSHOT_HEADER.pack(
            _encode_symbol(snapshot.symbol),
            bid_count,
            ask_count,
            snapshot.sequence,
            snapshot.timestamp_ns,
        )
        levels = bytearray()
        for price, qty, cnt in snapshot.bids:
            levels += BOOK_LEVEL_FRAME.pack(price, qty, cnt)
        for price, qty, cnt in snapshot.asks:
            levels += BOOK_LEVEL_FRAME.pack(price, qty, cnt)

        return bytes(header) + bytes(levels)

    @staticmethod
    def decode(data: bytes | bytearray) -> "BookSnapshot":
        """Reconstruct a depth snapshot supplied by a native/DMA backend."""
        from engine.core.order_book import BookSnapshot

        raw_symbol, bid_count, ask_count, sequence, timestamp_ns = SNAPSHOT_HEADER.unpack_from(
            data, 0
        )
        expected_size = BookSnapshotCodec.frame_size(bid_count, ask_count)
        if len(data) < expected_size:
            raise ValueError("snapshot frame is shorter than its level counts")

        offset = SNAPSHOT_HEADER.size
        bids: list[tuple[float, int, int]] = []
        asks: list[tuple[float, int, int]] = []
        for _ in range(bid_count):
            bids.append(BookLevelCodec.decode(data, offset))
            offset += BOOK_LEVEL_FRAME.size
        for _ in range(ask_count):
            asks.append(BookLevelCodec.decode(data, offset))
            offset += BOOK_LEVEL_FRAME.size

        return BookSnapshot(
            symbol=_decode_symbol(raw_symbol),
            sequence=sequence,
            bids=bids,
            asks=asks,
            timestamp_ns=timestamp_ns,
        )

    @staticmethod
    def frame_size(bid_count: int, ask_count: int) -> int:
        return SNAPSHOT_HEADER.size + (bid_count + ask_count) * BOOK_LEVEL_FRAME.size


# ── MatchResultCodec ──────────────────────────────────────────────────────────

class MatchResultCodec:
    """
    Encode/decode MatchResult to/from a variable-length binary frame.

    Layout: 8-byte header + N×64-byte TRADE_FRAMEs
      Header: trade_count(2) total_filled(4) status(1) is_resting(1)

    Max frame size: 8 + 256×64 = 16,392 bytes (256 fills is pathological;
    typical is 1–3 trades per order).
    """

    HEADER = struct.Struct(">H I B B")  # trade_count, total_filled, status, is_resting
    assert HEADER.size == 8

    @classmethod
    def encode(cls, result: "MatchResult") -> bytes:
        order = result.order
        header = cls.HEADER.pack(
            len(result.trades),
            result.total_filled,
            _STATUS_TO_WIRE[order.status],
            1 if result.is_new_resting else 0,
        )
        trade_frames = b"".join(TradeCodec.encode(t) for t in result.trades)
        return header + trade_frames

    @classmethod
    def decode(cls, data: bytes | bytearray) -> dict:
        """Partial decode: returns dict with scalars + trade dicts."""
        trade_count, total_filled, status_b, is_resting = cls.HEADER.unpack_from(data, 0)
        trades = []
        for i in range(trade_count):
            offset = cls.HEADER.size + i * TRADE_FRAME.size
            trades.append(TradeCodec.decode(data, offset))
        return {
            "trade_count": trade_count,
            "total_filled": total_filled,
            "status": _WIRE_TO_STATUS[status_b],
            "is_new_resting": bool(is_resting),
            "trades": trades,
        }


# ── Frame type tags (for multiplexed DMA channels) ────────────────────────────

class FrameType:
    """
    1-byte message type tag prepended to frames on multiplexed channels.

    In a PCIe DMA design with a single completion ring, the FPGA prefixes
    each frame with this tag so the host driver knows which codec to apply.
    """
    ORDER = 0x01
    TRADE = 0x02
    BOOK_SNAPSHOT = 0x03
    CANCEL_ACK = 0x04
    REJECT = 0x05
    HEARTBEAT = 0xFF
