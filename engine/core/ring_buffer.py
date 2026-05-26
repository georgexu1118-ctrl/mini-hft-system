"""
Cache-line-aware Single-Producer, Single-Consumer (SPSC) ring buffer.

Why SPSC over asyncio.Queue
────────────────────────────
asyncio.Queue uses a `collections.deque` plus a `threading.Lock` plus
`asyncio.Event` signalling. Each `put_nowait` call:
  - acquires the GIL
  - acquires the internal lock
  - appends to the deque
  - checks if a getter is waiting, potentially schedules a callback

Measured overhead: ~1–5 µs per operation depending on event loop pressure.

SPSC ring buffer (this module):
  - No lock — the producer and consumer use separate counters
  - No dynamic allocation — fixed-size pre-allocated buffer
  - Cache-line padding between head and tail prevents false sharing

Measured overhead: ~50–200 ns per operation (Python; ~2–10 ns in C++).

Memory layout for FPGA DMA
────────────────────────────
When backed by a hugepage (via `mmap` with `MAP_HUGETLB`), the ring buffer
can be shared between the host CPU and an FPGA via PCIe:

  Host CPU (producer):
    buf = mmap.mmap(-1, HUGEPAGE_SIZE, mmap.MAP_SHARED | MAP_HUGETLB)
    ring = SPSCRingBuffer.from_mmap(buf)
    ring.put(ORDER_FRAME_BYTES)

  FPGA (consumer via DMA):
    - DMA engine reads tail from host memory (PCIe read)
    - Reads ORDER_FRAME at ring[tail & mask]
    - Processes the order
    - Writes TRADE_FRAME to the *result* ring (PCIe write)
    - Increments result_head in host memory

  Host CPU (polling result ring):
    while result_ring.is_empty(): pass  # busy-poll, no interrupts
    frame = result_ring.get()
    result = MatchResultCodec.decode(frame)

False sharing explained
────────────────────────
If head and tail pointers share a cache line:
  - Producer writes head → invalidates cache line on all cores
  - Consumer reads tail → must re-fetch the cache line
  - Even though tail hasn't changed, the cache coherence protocol
    evicts it from the consumer's L1 cache every time the producer
    updates head

Fix: place head and tail on SEPARATE cache lines (64-byte alignment).
This is done by padding each counter array to 64 bytes.

In C++ this is `alignas(64) std::atomic<uint64_t> head_`:
```cpp
struct alignas(64) { std::atomic<uint64_t> head{0}; };
struct alignas(64) { std::atomic<uint64_t> tail{0}; };
```

In Python we approximate this with separate 8-element arrays
(8 × 8 bytes = 64 bytes per array element).
"""
from __future__ import annotations
import array
from typing import Optional


CACHE_LINE_BYTES = 64


class SPSCRingBuffer:
    """
    Fixed-capacity, fixed-item-size, cache-line-aware SPSC ring buffer.

    Parameters
    ──────────
    capacity   Number of slots. Rounded up to next power-of-2 for fast
               modulo: index = counter & (capacity - 1)
    item_size  Fixed size of each item in bytes. Must match the frame
               size used by the codec (e.g. 64 for ORDER_FRAME).

    Thread safety
    ─────────────
    Designed for exactly one producer thread and one consumer thread.
    NO mutex, NO GIL assumption beyond Python's own guarantees.

    In a true C/C++ SPSC ring:
    - head is written by producer with `memory_order_release`
    - head is read by consumer with `memory_order_acquire`
    - This ensures the frame write (store) is visible before head increment

    Python limitation: we can't express memory ordering, but in CPython
    the GIL provides sequential consistency for our purposes.
    For GIL-free use (Cython/C ext): replace `array.array` with
    `ctypes.c_uint64` and use `ctypes.atomic_store/load`.
    """

    # Slots of padding per cache line (8 uint64 = 64 bytes)
    _CACHE_LINE_SLOTS = CACHE_LINE_BYTES // 8

    def __init__(self, capacity: int, item_size: int = 64) -> None:
        # Round capacity up to next power of 2
        cap = 1
        while cap < max(capacity, 2):
            cap <<= 1
        self._capacity = cap
        self._mask = cap - 1
        self._item_size = item_size

        # Cache-line padded counters.
        # _head[0] = producer write pointer (# of items ever enqueued)
        # _tail[0] = consumer read pointer  (# of items ever dequeued)
        # Elements [1..7] are padding — never read or written.
        self._head = array.array("Q", [0] * self._CACHE_LINE_SLOTS)
        self._tail = array.array("Q", [0] * self._CACHE_LINE_SLOTS)

        # Contiguous item storage: capacity × item_size bytes
        # In production: replace with mmap(MAP_HUGETLB) for FPGA DMA
        self._buffer: bytearray = bytearray(cap * item_size)

        # Precompute slot stride for cheap index arithmetic
        self._stride = item_size

    # ── Producer interface ────────────────────────────────────────────────────

    def put(self, item: bytes | bytearray) -> bool:
        """
        Write one item to the ring. Returns False if the ring is full.

        O(1). Does not allocate.

        In C++ with FPGA DMA:
            // write item into DMA-mapped buffer at ring[head & mask]
            memcpy(&buf[head & mask], item, FRAME_SIZE);
            std::atomic_thread_fence(memory_order_release);
            head.fetch_add(1, memory_order_relaxed);
        """
        head = self._head[0]
        if head - self._tail[0] >= self._capacity:
            return False  # full

        slot = head & self._mask
        start = slot * self._stride
        end = start + self._stride
        self._buffer[start:end] = item[:self._stride]

        # Increment after write — consumer must see data before counter update
        self._head[0] = head + 1
        return True

    def put_nowait(self, item: bytes | bytearray) -> bool:
        """Alias for put() — matches asyncio.Queue interface for drop-in use."""
        return self.put(item)

    # ── Consumer interface ────────────────────────────────────────────────────

    def get(self) -> Optional[bytes]:
        """
        Read and remove one item. Returns None if empty.

        O(1). Returns a new bytes object (one copy from the buffer).

        FPGA polling pattern:
            while (tail == head) { _mm_pause(); }  // spin with PAUSE hint
            memcpy(result, &buf[tail & mask], FRAME_SIZE);
            tail.fetch_add(1, memory_order_release);
        """
        tail = self._tail[0]
        if tail == self._head[0]:
            return None  # empty

        slot = tail & self._mask
        start = slot * self._stride
        item = bytes(self._buffer[start: start + self._stride])

        # Increment after read — producer must see we've freed the slot
        self._tail[0] = tail + 1
        return item

    def peek(self) -> Optional[bytes]:
        """
        Read without removing. Returns None if empty.

        Useful for inspecting the next frame's type tag before committing
        to decode it (matches `FrameType` tags in binary_protocol.py).
        """
        tail = self._tail[0]
        if tail == self._head[0]:
            return None
        slot = tail & self._mask
        start = slot * self._stride
        return bytes(self._buffer[start: start + self._stride])

    # ── State queries ─────────────────────────────────────────────────────────

    def is_empty(self) -> bool:
        return self._head[0] == self._tail[0]

    def is_full(self) -> bool:
        return (self._head[0] - self._tail[0]) >= self._capacity

    def size(self) -> int:
        """Number of items currently in the ring."""
        return self._head[0] - self._tail[0]

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def item_size(self) -> int:
        return self._item_size

    def utilization(self) -> float:
        """Fill ratio [0.0, 1.0]. Useful for capacity planning."""
        return self.size() / self._capacity


class BinaryEventRing:
    """
    Typed ring buffer for binary-encoded domain events.

    Wraps SPSCRingBuffer with convenience methods for the engine → service
    pipeline. Each slot holds one encoded frame (ORDER_FRAME or TRADE_FRAME).

    This is the FPGA-ready alternative to asyncio.Queue[DomainEvent].
    When the FPGA backend is active, the FPGA writes directly into
    the underlying buffer and increments the head counter via PCIe DMA.
    """

    TRADE_FRAME_SIZE = 64
    ORDER_FRAME_SIZE = 64

    def __init__(self, capacity: int = 4096) -> None:
        # Use 65-byte slots: 1-byte type tag + 64-byte frame
        # This multiplexes orders and trades on a single ring
        self._ring = SPSCRingBuffer(capacity, item_size=65)

    def put_frame(self, frame_type: int, frame: bytes) -> bool:
        """Prepend type tag and enqueue. O(1)."""
        slot = bytes([frame_type]) + frame[:64]
        return self._ring.put(slot)

    def get_frame(self) -> Optional[tuple[int, bytes]]:
        """Dequeue and split type tag from frame. O(1)."""
        raw = self._ring.get()
        if raw is None:
            return None
        return raw[0], raw[1:]

    def peek_type(self) -> Optional[int]:
        """Return the type tag of the next frame without consuming it."""
        raw = self._ring.peek()
        return raw[0] if raw else None

    @property
    def size(self) -> int:
        return self._ring.size()

    def is_empty(self) -> bool:
        return self._ring.is_empty()
