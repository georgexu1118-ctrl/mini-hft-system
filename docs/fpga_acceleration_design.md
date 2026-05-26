# FPGA Acceleration Design — Mini HFT System

> **Status**: Design specification. No FPGA code exists yet.
> This document defines exactly how FPGA acceleration will be integrated
> when Milestone 8 begins. It is written to be actionable by an engineer
> starting from zero FPGA knowledge but strong systems engineering background.

---

## 1. Why FPGA for HFT?

FPGA (Field-Programmable Gate Array) is reconfigurable silicon — you program
it with hardware description language (HDL: VHDL or Verilog/SystemVerilog)
and the result is a custom circuit that runs your algorithm in dedicated logic,
not in a CPU pipeline.

### Latency comparison for a limit order (resting, no fill):

| Implementation | Processing Latency | Why |
|---|---|---|
| This project (Python) | ~350 µs | Object allocation, GC, interpreter overhead |
| Optimised C++ | ~500 ns | Cache-friendly structs, no GC, SIMD |
| C++ with kernel bypass | ~200 ns | DPDK, bypasses OS network stack |
| FPGA | ~50–150 ns | Dedicated silicon, pipeline latency = clock cycles |

The FPGA wins because:
- **No OS**: no scheduler, no context switches, no IRQ latency jitter
- **No CPU pipeline**: no branch prediction misses, no out-of-order reordering
- **Parallelism**: price level lookup and FIFO queue operations run in parallel
  in separate logic blocks, simultaneously, every clock cycle
- **Deterministic**: FPGA latency is clock-cycle-deterministic, not probabilistic

Typical production numbers (Xilinx Alveo U250, co-located at exchange):
- Order → match → ack: 70–120 ns
- Feed parse → book update: 30–60 ns
- Strategy signal → order: 80–200 ns

---

## 2. Current Architecture Latency Budget

```
REST client                          Exchange match engine
    │                                        │
    │ ~1 ms (network, TCP)                   │
    ▼                                        │
FastAPI gateway                              │
    │ ~10 µs (JSON parse, Pydantic)          │
    ▼                                        │
OrderService.submit()                        │
    │ ~1 µs (object construction)            │
    ▼                                        │
MatchingEngine.submit_order()   ← HOT PATH  │
    │ ~350 µs (Python CLOB)                  │
    ▼                                        │
EventBus.publish_nowait()                    │
    │ ~1 µs (asyncio queue)                  │
    ▼                                        │
WS broadcast                                │
    │ ~50 µs (JSON serialise, WebSocket)     │
    ▼                                        │
    └─────────────────────────────────────────
```

**Total observed roundtrip: ~1–2 ms (REST-based)**

Target with FPGA matching + binary UDP ingress: **< 500 ns** (order → ack).
This is achievable because we eliminate the REST round-trip entirely and
replace the Python matching with dedicated silicon.

---

## 3. Latency-Critical Paths (Ranked by Impact)

### P1 — Matching Engine (Highest Impact)

**Current**: Python CLOB in `engine/core/order_book.py`
**Bottleneck**: Object allocation (~300 µs/order), SortedDict operations (~50 µs)
**FPGA target**: BRAM-indexed price level array, hardware FIFO queues

The matching algorithm maps cleanly to hardware because:
- Price levels are indexed by a deterministic integer (price × tick_size)
- FIFO queues at each level can be hardware FIFO primitives
- The matching loop is a simple walk from best to worst price — sequential BRAM access

### P2 — Feed Handler / Packet Parser

**Current**: Python GBM generator (not a real bottleneck since it's synthetic)
**Real bottleneck**: On a live exchange feed, parsing raw UDP packets in Python
**FPGA target**: Hardware ITCH/MDP3 parser

Real numbers from Nasdaq ITCH 5.0:
- Peak: 5 million messages/second (NYSE open)
- Message size: 20–50 bytes
- Required parse time per message: 200 ns or less

Python can parse ~100k messages/sec. FPGA parses all 5M with deterministic latency.

### P3 — Order Ingress Channel

**Current**: HTTP REST + JSON (adds ~1 ms latency for serialisation + TCP RTT)
**FPGA target**: Direct memory-mapped write from co-located strategy
  - Strategy writes ORDER_FRAME (64 bytes) to hugepage at known physical address
  - FPGA reads via DMA, routes to matching pipeline
  - No network, no OS, no CPU in the ingress path

### P4 — Risk Engine

**Current**: Python comparisons in `engine/risk/limits.py` (~500 ns)
**FPGA target**: Single-cycle comparators against hardware registers (~4 ns at 250 MHz)
- Price within [min, max]: 1 comparator, 1 cycle
- Quantity within [0, max]: 1 comparator, 1 cycle
- Notional = price × qty < limit: 1 multiplier + 1 comparator, ~3 cycles

---

## 4. Target Architecture (with FPGA)

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         Host Server (x86-64 Linux)                       │
│                                                                           │
│  ┌─────────────────────┐    ┌─────────────────────────────────────────┐  │
│  │  FastAPI Gateway     │    │           FPGA Driver Process            │  │
│  │  (REST + WebSocket   │    │                                          │  │
│  │   for browser UI)   │    │  ┌──────────────┐  ┌────────────────┐   │  │
│  │                     │    │  │ Order Ingress │  │ Completion Ring │   │  │
│  │  Non-HFT clients:   │    │  │ Ring (write) │  │  (read/poll)   │   │  │
│  │  ~1ms latency OK    │    │  └──────┬───────┘  └───────┬────────┘   │  │
│  └─────────────────────┘    │         │                   │            │  │
│                              └─────────┼───────────────────┼────────────┘  │
└──────────────────────────────────────────────────────────────────────────┘
                                         │ PCIe x8 Gen4 (16 GB/s, ~100 ns DMA)
               ──────────────────────────┼──────────────────────────────────
                                         │
┌────────────────────────────────────────┼──────────────────────────────────┐
│                     FPGA (Xilinx Alveo U250)                               │
│                                        │                                   │
│  ┌──────────────┐   ┌──────────────────▼────────────────────────────┐     │
│  │  100G NIC    │   │              PCIe DMA Engine (Xilinx XDMA IP)   │     │
│  │  (UDP RX)    │   │  Reads order frames from host ring              │     │
│  └──────┬───────┘   │  Writes trade/ack frames to host completion    │     │
│         │           └──────────────────────────────────────────────┬─┘     │
│         │                                                           │       │
│  ┌──────▼───────────────────────────────────────────────────┐      │       │
│  │                   Order Pipeline (FPGA fabric)            │      │       │
│  │                                                           │      │       │
│  │  ┌─────────────┐   ┌──────────────┐   ┌─────────────┐   │      │       │
│  │  │ Feed Parser  │──►│ Risk Engine  │──►│   CLOB Core │──►│      │       │
│  │  │ (ITCH/MDP3) │   │ (comparators)│   │             │   │      │       │
│  │  └─────────────┘   └──────────────┘   │ Price Levels│   │      │       │
│  │                                        │ (BRAM array)│   │      │       │
│  │  ┌─────────────┐                       │ FIFO Queues │   │      │       │
│  │  │ DMA Ingress │──────────────────────►│ per Level   │   │      │       │
│  │  │ (from CPU)  │                       └──────┬──────┘   │      │       │
│  │  └─────────────┘                              │           │      │       │
│  └─────────────────────────────────────────── ───┼───────────┘      │       │
│                                                   └──────────────────┘       │
│                                                   Trade/Ack frames           │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## 5. PCIe / DMA Integration Design

### 5.1 Physical Layer

Modern HFT FPGAs connect via PCIe Gen4 x16 or x8:
- Gen4 x8: 8 lanes × 2 GB/s = **16 GB/s** peak bandwidth
- DMA latency: ~100–300 ns (measured, not theoretical)
- CPU-initiated transfer: CPU writes to PCIe BAR → FPGA sees it in ~100 ns

### 5.2 Memory Architecture

```
Host physical memory layout (hugepages, 2 MB each):

Page 0 (order submission ring):
  [0x0000 – 0x7FFF] = 16384 × 64-byte ORDER_FRAME slots = 1 MB
  [0x8000 – 0x8007] = head counter (uint64, written by CPU)
  [0x8008 – 0x800F] = tail counter (uint64, written by FPGA via DMA)

Page 1 (completion ring — trades and acks):
  [0x0000 – 0x7FFF] = 16384 × 65-byte typed frames (1 tag + 64 data)
  [0x8000 – 0x8007] = head counter (uint64, written by FPGA via DMA)
  [0x8008 – 0x800F] = tail counter (uint64, written by CPU)

Page 2 (book snapshot ring):
  [0x0000 – 0x3FFF] = 4096 × 352-byte snapshot frames (header + 20 levels)
  [0x4000 – 0x4007] = head counter (FPGA writes)
  [0x4008 – 0x400F] = tail counter (CPU reads)
```

### 5.3 Host-Side Driver Pseudocode (Python shim for FPGA HAL)

```python
# engine/hal/fpga_hal.py (M8 — not yet implemented)
import mmap, ctypes, os

class FPGAMatchingHAL(MatchingHAL):
    HUGEPAGE_SIZE = 2 * 1024 * 1024  # 2 MB

    def __init__(self, device_path: str = "/dev/xdma0_user"):
        # Map FPGA BAR (control registers)
        fd = os.open(device_path, os.O_RDWR | os.O_SYNC)
        self._bar = mmap.mmap(fd, 0x10000)

        # Allocate hugepage-backed DMA rings
        # In production: mmap with MAP_HUGETLB | MAP_ANONYMOUS
        self._order_ring = _alloc_hugepage(self.HUGEPAGE_SIZE)
        self._completion_ring = _alloc_hugepage(self.HUGEPAGE_SIZE)

        # Tell FPGA the physical addresses (via PCIe BAR register write)
        phys_order = _virt_to_phys(self._order_ring)
        phys_complete = _virt_to_phys(self._completion_ring)
        self._bar.write_at(0x100, phys_order.to_bytes(8, "little"))
        self._bar.write_at(0x108, phys_complete.to_bytes(8, "little"))

        self._head = ctypes.c_uint64.from_buffer(self._order_ring, 0x8000)
        self._comp_head = ctypes.c_uint64.from_buffer(self._completion_ring, 0x8000)
        self._comp_tail = ctypes.c_uint64.from_buffer(self._completion_ring, 0x8008)

    def submit_order(self, order: Order) -> MatchResult:
        # Encode to binary frame
        frame = OrderCodec.encode(order)

        # Write to DMA ring (CPU writes to hugepage memory)
        slot = self._head.value & 0x3FFF   # mask for 16384-slot ring
        offset = slot * 64
        ctypes.memmove(
            ctypes.c_char_p(ctypes.addressof(self._order_ring) + offset),
            frame, 64
        )
        # Advance head — FPGA sees this and starts processing
        self._head.value += 1              # atomic on x86 (store release)

        # Busy-poll completion ring (< 300 ns typical on PCIe Gen4)
        tail = self._comp_tail.value
        while self._comp_head.value == tail:
            pass  # spin; add _mm_pause() equivalent in C extension

        # Read result frame
        slot = tail & 0x3FFF
        result_bytes = bytes(
            (ctypes.c_char * 65).from_buffer(self._completion_ring, slot * 65)
        )
        self._comp_tail.value += 1
        return MatchResultCodec.decode(result_bytes)
```

### 5.4 FPGA-Side DMA Engine

Uses Xilinx XDMA or CDMA IP core:
```
XDMA configuration (xdma_0):
  - PCIe lanes: x8 Gen4
  - AXI4-Stream data width: 512 bits (64 bytes/cycle at 250 MHz = 16 GB/s)
  - Descriptor bypass mode: FPGA controls DMA transfers directly
  - Completion interrupt: disabled (polling is faster than interrupt latency)
```

---

## 6. Feed Handler FPGA Migration

### 6.1 Nasdaq ITCH 5.0 on FPGA

ITCH 5.0 is a binary UDP multicast protocol. Each message is 20–50 bytes.
Message types relevant to order book construction:

```
Type 0x41 'A' — Add Order
  Length: 36 bytes
  Fields: StockLocate(2) TrackingNumber(2) Timestamp(6) OrderRef(8)
          BuySellIndicator(1) Shares(4) Stock(8) Price(4) ←─ all fixed-size!

Type 0x44 'D' — Delete Order
  Length: 19 bytes
  Fields: StockLocate(2) TrackingNumber(2) Timestamp(6) OrderRef(8)

Type 0x45 'E' — Order Executed
  Length: 31 bytes
  Fields: StockLocate(2) TrackingNumber(2) Timestamp(6) OrderRef(8)
          ExecutedShares(4) MatchNumber(8)
```

### 6.2 FPGA Parser Pipeline (HDL pseudocode)

```verilog
// Receives AXI4-Stream bytes from 100G MAC
// Outputs parsed BookUpdate structs

module itch_parser (
    input  logic        clk, rst_n,
    input  logic [7:0]  rx_data,
    input  logic        rx_valid,
    output BookUpdate_t book_update,
    output logic        update_valid
);
    // State machine: IDLE → MSG_TYPE → FIELDS → EMIT
    // Each state lasts exactly 1 clock cycle (250 MHz → 4 ns/cycle)
    // Total parse latency: ~20 cycles = 80 ns
    
    always_ff @(posedge clk) begin
        case (state)
            IDLE:     if (rx_valid) begin
                          msg_type <= rx_data;
                          state <= PARSE_HEADER;
                      end
            PARSE_...: // field-by-field extraction
            EMIT:      begin
                          update_valid <= 1;
                          book_update.price <= price_field;
                          book_update.qty   <= qty_field;
                          state <= IDLE;
                      end
        endcase
    end
endmodule
```

### 6.3 Integration with Python FeedHandlerBase

The FPGA writes parsed `BookUpdate` structs to a shared DMA ring.
A Python polling loop reads from that ring and calls `on_book_update()`:

```python
class FPGAFeedHandler(FeedHandlerBase):
    async def run(self) -> None:
        while True:
            # Poll DMA completion ring (non-blocking)
            frame = self._update_ring.get()
            if frame:
                update = BookUpdateCodec.decode(frame)
                self.on_book_update(update.symbol, update.snapshot)
            else:
                # Yield to event loop briefly when ring is empty
                await asyncio.sleep(0)  # 0 = yield only, no timer delay

    def on_book_update(self, symbol: Symbol, snapshot: BookSnapshot) -> None:
        self._event_bus.publish_nowait(BookUpdateEvent(
            symbol=symbol, snapshot=snapshot,
            timestamp_ns=time.time_ns(),
        ))
```

---

## 7. Binary Protocol Optimisations

### 7.1 JSON → Fixed Binary

| Metric | JSON (current) | Fixed Binary |
|--------|---------------|-------------|
| Order message size | ~200 bytes | 64 bytes (3× smaller) |
| Parse time (Python) | ~5–15 µs | ~500 ns |
| Parse time (C++) | ~1–3 µs | ~50 ns |
| Parse time (FPGA) | N/A | ~4 ns |
| Cache lines occupied | 3–4 | 1 |
| Allocations per message | Several (dict, strings) | Zero (fixed buffer) |

### 7.2 Struct Alignment Rules

1. **64-byte frames** — one cache line. The entire message fits in a single
   L1 cache fetch (4 ns). A 65-byte message requires two cache line fetches.

2. **Natural alignment** — float64 at offset divisible by 8, uint32 at
   divisible by 4. Misaligned reads require two memory operations on some architectures.

3. **Big-endian** — standard for network protocols. FPGA always uses big-endian.
   x86 CPUs are little-endian but the `bswap` instruction costs 1 cycle.

4. **Reserved padding** — always reserve 6–8 bytes at the end of each struct.
   Future fields can use this space without breaking the struct size invariant.

### 7.3 SBE (Simple Binary Encoding)

For production exchange-facing integration, consider SBE (used by CME, CBOE):
- Fixed-size fields, no variable-length encoding
- Schema-described for code generation
- Used by the FIX protocol Simple Binary Encoding standard
- C++ codegen exists (real-logic/simple-binary-encoding)

For this project: our custom binary format in `binary_protocol.py` is
sufficient and more educational. SBE becomes relevant when integrating
with live exchange APIs.

---

## 8. Memory Layout Improvements

### 8.1 Order Object Memory Map (Current Python)

```
Python Order object on the heap:
  __slots__-based layout (no __dict__, due to slots=True):

  PyObject header:   16 bytes  (ob_refcnt + ob_type)
  slot pointers:   ~200 bytes  (12 slots × ~8 bytes each, with Python object refs)
  PyUnicode (symbol):  ~70 bytes  (Python string header + chars)
  PyUnicode (order_id): ~75 bytes (UUID string)
  PyFloat (price):      ~24 bytes
  PyLong (quantity):    ~28 bytes
  ...
  Total: ~500 bytes per Order object, scattered across the heap
```

```
Binary ORDER_FRAME (target for FPGA path):
  Contiguous 64 bytes, pre-allocated in a pool:
  - Fits in 1 cache line
  - Zero allocation on submit (write into pool slot)
  - FPGA reads directly via DMA
```

### 8.2 Order Pool (C++ M7 target)

```cpp
// Pre-allocated pool eliminates all hot-path allocation
struct alignas(64) OrderFrame {
    uint8_t  order_id[16];
    double   price;
    uint32_t quantity;
    uint32_t filled_qty;
    uint64_t created_at_ns;
    uint64_t processed_ns;
    char     symbol[6];
    uint8_t  side, order_type, tif, status;
    uint8_t  _reserved[6];
};

class OrderPool {
    static constexpr size_t POOL_SIZE = 65536;
    OrderFrame frames_[POOL_SIZE];      // 65536 × 64 = 4 MB, pre-faulted
    std::atomic<uint32_t> head_{0};
    
    OrderFrame* alloc() noexcept {
        uint32_t idx = head_.fetch_add(1, std::memory_order_relaxed);
        return &frames_[idx & (POOL_SIZE - 1)];  // ring: no free() needed
    }
};
```

### 8.3 Price Level BRAM Layout (FPGA M8 target)

```
On FPGA, the order book is a BRAM array indexed by price tick:

  Tick index = (price_cents - MIN_PRICE_CENTS)

  BRAM layout:
    Address 0x0000: PriceLevel for $0.01
    Address 0x0001: PriceLevel for $0.02
    ...
    Address 0x3A98: PriceLevel for $150.00 (AAPL region)
    ...
    Address 0xFFFF: PriceLevel for $655.35

  Each PriceLevel entry (64 bytes):
    total_qty(4) order_count(4) head_ptr(4) tail_ptr(4) flags(4) reserved(44)

  FIFO queues for each level stored in a separate BRAM region:
    Each queue slot: 64-byte OrderFrame
    16 slots per price level = 1 KB per level
    65536 levels × 1 KB = 64 MB (fits in Alveo U250 HBM)

Lookup: O(1) by price tick (BRAM read = 1 clock cycle = 4 ns at 250 MHz)
vs Python SortedDict: O(log n) = ~10 comparisons = ~5 µs
```

---

## 9. Implementation Roadmap to FPGA

### Step 1: Binary Protocol (✅ Done — M1/2 refactor)
All domain objects implement `to_bytes()` / `from_bytes()`.
`engine/core/binary_protocol.py` defines the wire format.

### Step 2: HAL Abstraction (✅ Done — M1/2 refactor)
`engine/hal/abstract.py` defines the interface.
`engine/hal/software.py` is the working implementation.
Service layer imports `MatchingHAL`, not `MatchingEngine`.

### Step 3: Ring Buffer Infrastructure (✅ Done — M1/2 refactor)
`engine/core/ring_buffer.py` implements cache-line-aware SPSC.
Hot path can use ring buffer instead of asyncio.Queue.

### Step 4: C++ Matching Engine (M7)
- Implement `OrderBook` and `MatchingEngine` in C++
- Expose via `ctypes` or `pybind11`
- Implement `CppMatchingHAL(MatchingHAL)` wrapping the C extension
- Swap in `api/main.py`: `CppMatchingHAL()` instead of `SoftwareMatchingHAL()`
- **Zero changes to OrderService, routers, or WebSocket code**

### Step 5: FPGA Integration (M8)
- Implement `FPGAMatchingHAL(MatchingHAL)` using hugepage DMA rings
- Implement HDL for matching pipeline (Xilinx Vivado HLS or RTL)
- Implement HDL for feed parser (ITCH 5.0)
- Swap in `api/main.py`: `FPGAMatchingHAL("/dev/xdma0")` 
- **Zero changes anywhere else**

---

## 10. Performance Measurement Plan

Instrument the following at each milestone:

```python
# At every order lifecycle point, record:
order.created_at_ns    # T0: client intent
order.received_at_ns   # T1: gateway receipt  → (T1-T0) = network + deserialise
order.queued_at_ns     # T2: engine queue     → (T2-T1) = gateway overhead
order.processed_at_ns  # T3: match complete   → (T3-T2) = ENGINE LATENCY
order.acked_at_ns      # T4: ack sent         → (T4-T3) = serialise + dispatch
```

Engine latency (T3-T2) is the number we optimise. Expected trajectory:

```
Python (now):      ~350 µs  → p99: ~1000 µs
C++ (M7):          ~0.5 µs  → p99: ~2 µs
FPGA (M8):         ~0.15 µs → p99: ~0.3 µs
```

Run benchmarks with:
```bash
pytest tests/benchmarks/ -v --benchmark-sort=mean
```
(Create `tests/benchmarks/` in M4.)

---

## 11. Hardware Recommendations

For M8 FPGA development:

| Board | Cost | PCIe | Best for |
|-------|------|------|---------|
| Xilinx Alveo U250 | $10k | Gen4 x16 | Production deployment |
| Xilinx Alveo U50 | $3k | Gen3 x16 | Development, similar tools |
| Intel Stratix 10 GX | $15k | Gen4 x16 | Alternative vendor |
| Xilinx KV260 (dev kit) | $200 | No PCIe | Learning HDL only |
| AWS F1 instance | ~$13/hr | via ENA | Cloud FPGA prototyping |

For this educational project: **AWS F1** is the lowest-barrier path to running
real HDL. No hardware purchase, pay-per-use, Xilinx VU9P chip, Vivado toolchain.

---

## Appendix A: Glossary

| Term | Definition |
|------|-----------|
| BRAM | Block RAM — dedicated on-chip SRAM in FPGAs, 1-cycle access |
| BAR | Base Address Register — PCIe device memory region visible to host CPU |
| DMA | Direct Memory Access — device reads/writes host memory without CPU |
| HBM | High Bandwidth Memory — stacked DRAM on advanced FPGAs (e.g. Alveo U280) |
| ITCH | Nasdaq's binary market data protocol (UDP multicast) |
| MDP3 | CME's Market Data Platform 3.0 binary protocol |
| SBE | Simple Binary Encoding — FIX-standard compact binary encoding |
| SPSC | Single Producer Single Consumer — lock-free ring buffer topology |
| CLOB | Central Limit Order Book — price-time priority matching engine |
| PCIe | Peripheral Component Interconnect Express — FPGA↔CPU interconnect |
| XDMA | Xilinx DMA IP core — standard PCIe DMA engine for Xilinx FPGAs |
