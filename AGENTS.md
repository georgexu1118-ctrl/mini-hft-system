# AGENTS.md — Mini HFT System

> **Read this file before making any changes.**
> It encodes the architectural invariants and coding discipline that keep
> this codebase production-grade. Violating these rules silently breaks
> the latency model, the FPGA upgrade path, or the service boundary contract.

---

## Project Purpose

A realistic low-latency trading simulator designed to teach (and demonstrate)
**systems engineering thinking**, not just coding. Every design decision mirrors
patterns used in production HFT infrastructure. The educational value comes from
understanding *why* decisions are made, not just *what* the code does.

**Audience**: HFT infrastructure engineers, quant developers, systems programmers
who care about microsecond-level latency, hardware acceleration, and correctness
under concurrent access.

---

## Critical Architecture Rules

### 1. Hot path has ZERO I/O — NEVER violate this

```
HOT PATH: order_in → validate → match → result_out
```

The matching engine (`engine/core/matching_engine.py`, `engine/core/order_book.py`)
must never contain:
- `await` / `async` keywords
- Database calls
- Network calls
- File I/O
- `print()` or logging (in the submission path)
- External library calls with unknown latency

The HAL callback (`_emit`) may call `queue.put_nowait()` — that is the ONLY
permitted side effect on the hot path.

### 2. Engine is a pure computation module

`engine/` has **no dependency** on `api/`, `infra/`, or `shared/events.py`.
The import graph is strictly one-directional:

```
api → shared → engine
         ↑
       infra
```

`engine/` imports only from `engine/` itself and the Python standard library.
If you find yourself importing FastAPI, SQLAlchemy, or asyncio into `engine/`,
you are breaking the architecture.

### 3. The HAL seam is sacred

`engine/hal/abstract.py` defines `MatchingHAL`. This is the **only interface**
the service layer (`api/services/order_service.py`) is allowed to use to reach
the matching engine. The service layer must never import `MatchingEngine` directly.

This seam is how we will later swap in a C++ extension or FPGA driver without
touching any API code.

### 4. Binary protocol for any new inter-process messaging

All new message formats that cross a process or hardware boundary must use the
fixed-size binary structs defined in `engine/core/binary_protocol.py`.
No JSON, no pickle, no protobuf (yet — until we need schema evolution).

Fixed-size structs are cache-line aligned (64 bytes) and trivially DMA-able.

### 5. Ring buffers over asyncio.Queue for latency-sensitive paths

`asyncio.Queue` has ~1–5 µs overhead per operation due to event loop scheduling.
For anything on or near the hot path, use `engine/core/ring_buffer.py`'s
`SPSCRingBuffer`. The asyncio queue is fine for the cold path (WS broadcasting,
DB writes).

### 6. Timestamps in nanoseconds everywhere

All timestamps must use `time.time_ns()`. Never use `time.time()` (float seconds)
or `datetime.now()` in the engine path — float precision is insufficient and
`datetime` allocates objects.

### 7. `slots=True` on all hot-path dataclasses

Any dataclass that is instantiated per-order must use `@dataclass(slots=True)`.
This eliminates the `__dict__` overhead (one dict allocation per object).
For 10k orders/sec this saves ~10 MB/sec of GC pressure.

---

## Module Ownership & Responsibility

| Module | Owns | Must NOT contain |
|--------|------|-----------------|
| `engine/core/` | Matching algorithm, Order/Trade types | I/O, async, DB, API |
| `engine/hal/` | Hardware abstraction layer | Business logic, event bus |
| `engine/market_data/` | Feed generation/parsing | Matching logic |
| `engine/risk/` | Pre-trade checks | Post-trade logic |
| `engine/transport/` | Ingress/egress protocols | Business logic |
| `shared/events.py` | Domain events, EventBus | Transport details |
| `shared/constants.py` | System-wide constants | Logic of any kind |
| `api/services/` | Bridge: engine ↔ async | Direct engine imports |
| `api/routers/` | HTTP/WS handlers | Business logic |
| `api/websocket/` | WS connection mgmt | Matching logic |
| `infra/` | Config, DB setup | Business logic |
| `strategy/` | Trading logic | Infrastructure details |

---

## Coding Standards

### Naming
- Order lifecycle variables: `order`, `result`, `trade` (not `o`, `r`, `t`)
- Latency variables: always suffix `_ns` for nanoseconds, `_us` for microseconds
- FPGA-facing structs: suffix `_wire` or `_frame` (e.g., `ORDER_WIRE_FORMAT`)
- Abstract classes: prefix `Abstract` or suffix `Base` (`MatchingHAL`, `FeedHandlerBase`)
- Protocol classes: no prefix/suffix (`MatchingBackend`, `OrderIngress`)

### Functions
- Hot-path functions must have a `# O(...)` complexity comment
- Any function that changes latency characteristics needs a docstring note
- Never use `**kwargs` in hot-path code (dict unpacking overhead)

### Files
- Maximum file length: 400 lines. Split if longer.
- One primary class or module concept per file.
- Never put matching engine logic in a router or service file.

### Types
- All public functions must have full type annotations
- Use `Protocol` (structural subtyping) for interfaces that FPGA/C++ will implement
- Use `ABC` for base classes with shared implementation logic
- `Optional[X]` → `X | None` (Python 3.10+ syntax throughout)

---

## FPGA Readiness Rules

These rules ensure the codebase can cleanly integrate an FPGA backend without
a full rewrite.

### R1 — Interface via HAL, not concrete engine
Service layer must reference `MatchingHAL` (abstract), not `MatchingEngine` (concrete).
Adding an FPGA HAL = implement `MatchingHAL`, swap in `api/main.py`. Nothing else changes.

### R2 — Binary codec for every domain object
Every object that crosses a HAL boundary must implement:
```python
def to_bytes(self) -> bytes: ...           # encode to fixed-size binary
@classmethod
def from_bytes(cls, data: bytes) -> Self: ... # decode from fixed-size binary
```
These are the DMA-safe representations.

### R3 — All queues must be replaceable with ring buffers
Design queue usage so the consumer only calls `.get()` / `.put_nowait()`.
Never use asyncio.Queue-specific APIs (`.join()`, `.task_done()`, `qsize()`).
This allows swapping in `SPSCRingBuffer` or a mmap'd hugepage buffer.

### R4 — No variable-length data in hot-path structs
Fixed-size only. No Python lists, strings, or dicts in hot-path data structures.
Symbol is 6 bytes padded. Order ID is 16 bytes (UUID bytes, not string).

### R5 — Separate feed parsing from feed consumption
Feed handler parses raw data → emits events. Consumer processes events.
This maps directly to: FPGA parses UDP → writes to DMA buffer → CPU consumes.

### R6 — Document all allocation points on the hot path
Every `Order(...)` constructor call is an allocation. Comment it.
The FPGA path eliminates allocations via pre-allocated order pools.

---

## Performance Targets

| Metric | Python (current) | C++ target | FPGA target |
|--------|-----------------|-----------|------------|
| Order resting (no fill) | ~350 µs | < 1 µs | < 200 ns |
| 1-fill match | ~900 µs | < 2 µs | < 500 ns |
| Book snapshot (10 levels) | ~50 µs | < 500 ns | < 100 ns |
| p99 processing latency | ~1000 µs | < 5 µs | < 1 µs |

*Python numbers are dominated by object allocation and GC, not the algorithm.*
*C++ with pre-allocated order pools eliminates allocation from the hot path.*
*FPGA eliminates OS scheduling jitter and processes in dedicated silicon.*

---

## Testing Requirements

- Every new module must have a corresponding test file in `tests/`
- Hot-path modules (`engine/core/`) require 100% branch coverage
- New abstract interfaces must have a conformance test (verify implementation satisfies protocol)
- Latency benchmarks live in `tests/benchmarks/` — never in unit test files
- Tests must pass with `pytest tests/ -v` from the project root

---

## Milestone Checklist (for agents working on future features)

- [ ] **M4** Async DB writer (drain TradeEvent/OrderFilledEvent → PostgreSQL)
  - Files: `infra/db/writer.py`, modify `api/main.py` lifespan
  - Rule: DB writer is a cold-path subscriber. NEVER block the EventBus.

- [ ] **M5** Strategy executor
  - Files: `strategy/executor.py`
  - Rule: executor calls `OrderService.submit()`, never engine directly.

- [ ] **M6** Next.js dashboard
  - Files: `frontend/` — Next.js 16 + TypeScript + Tailwind
  - WS message types are in `api/websocket/schemas.py` — use them as the contract.

- [ ] **M7** C++ engine binding
  - Files: `engine/hal/cpp_hal.py` (Python wrapper around C extension)
  - Rule: implement `MatchingHAL`. Zero changes to `OrderService` or API.

- [ ] **M8** FPGA integration
  - Files: `engine/hal/fpga_hal.py`
  - See: `docs/fpga_acceleration_design.md` for full integration spec.

---

## What Agents Should NOT Do

- **Do not** import `MatchingEngine` in `api/services/` — use `MatchingHAL`
- **Do not** add `await` inside `engine/core/`
- **Do not** use `time.time()` — always `time.time_ns()`
- **Do not** use JSON for inter-process messaging — use binary protocol
- **Do not** add business logic to routers — routers are thin HTTP adapters
- **Do not** add new global variables — use `app.state` and FastAPI DI
- **Do not** skip the binary codec requirement for new domain objects
- **Do not** make `asyncio.Queue` calls other than `.get()` / `.put_nowait()` / `.put()`
- **Do not** change the matching algorithm without a benchmark showing no regression
- **Do not** add dependencies to `requirements.txt` without a justification comment

---

## Quick Reference: Key Files

```
engine/core/matching_engine.py  ← The hot path. Treat like production code.
engine/core/order_book.py       ← Price-time priority CLOB. Read before modifying.
engine/core/binary_protocol.py  ← Fixed-size binary formats for DMA/FPGA.
engine/core/ring_buffer.py      ← Lock-free SPSC queue. Cache-line aware.
engine/hal/abstract.py          ← THE seam between software and hardware.
engine/hal/software.py          ← Current implementation of the HAL.
shared/events.py                ← EventBus. Never block inside a callback.
api/services/order_service.py   ← Bridge between async API and sync engine.
api/main.py                     ← Startup, background tasks, app factory.
docs/fpga_acceleration_design.md ← Full FPGA integration spec.
```
