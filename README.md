# Mini HFT System

A realistic low-latency trading simulator built to professional HFT infrastructure standards. Features a real Central Limit Order Book (CLOB) matching engine, live WebSocket market data feed, latency profiling, and a strategy sandbox — all designed to be extended toward C++/FPGA.

> **Purpose**: Educational + portfolio project demonstrating systems-engineering thinking, not just coding. Every design decision mirrors patterns used in production HFT infrastructure.

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Clients (Browser / Strategy Bot)            │
│           REST POST /orders    WebSocket /api/v1/market-data/ws     │
└───────────────────────┬────────────────────────┬────────────────────┘
                        │ HTTP                   │ WS frames
┌───────────────────────▼────────────────────────▼────────────────────┐
│                     FastAPI Gateway  (api/main.py)                   │
│   OrderRouter   MarketDataRouter   MetricsRouter   WS Manager        │
└───────────────────────┬────────────────────────┬────────────────────┘
                        │                        │
         ┌──────────────▼──────────┐   ┌─────────▼──────────────┐
         │    OrderService          │   │  ws_broadcast_loop      │
         │  (translate DTOs,        │   │  (drain EventBus →      │
         │   call engine,           │   │   serialise → push WS)  │
         │   publish events)        │   └─────────────────────────┘
         └──────────────┬──────────┘
                        │ synchronous call
         ┌──────────────▼──────────────────────────┐
         │         MatchingEngine                   │  ← HOT PATH
         │   submit_order()  cancel_order()         │  No I/O. No async.
         │                                          │  Pure computation.
         │   ┌──────────┐  ┌──────────┐            │
         │   │ OrderBook│  │ OrderBook│  ...        │
         │   │  (AAPL)  │  │  (TSLA)  │            │
         │   └────┬─────┘  └──────────┘            │
         └────────┼────────────────────────────────-┘
                  │ EngineEvent (synchronous callback)
         ┌────────▼────────────────────────────────┐
         │         EventBus  (in-process)           │  ← COLD PATH
         │   asyncio.Queue per event type           │  I/O OK here.
         │   publish_nowait → never blocks engine   │
         └──────┬──────────────┬───────────────────┘
                │              │
   ┌────────────▼──┐    ┌──────▼────────────────┐
   │ WS Broadcaster│    │  DB Writer (future)    │
   │ (drains queue)│    │  (async SQLAlchemy)    │
   └───────────────┘    └────────────────────────┘

       ┌─────────────────────────────────────┐
       │   MarketDataPublisher (background)  │
       │   GBM price feed → TickerEvents     │
       │   → EventBus → WS clients           │
       └─────────────────────────────────────┘
```

---

## Key Design Decisions

### 1. Hot Path / Cold Path Separation

The single most important HFT design principle: **the matching engine never touches I/O**.

```
HOT PATH   order_in → validate → match → result_out   (~2-15 µs Python, <1 µs C++)
COLD PATH  result  → persist → broadcast              (milliseconds, I/O OK)
```

The engine emits events through a synchronous callback. That callback calls `queue.put_nowait()` — a nanosecond-scale operation. The actual DB write and WebSocket push happen in background async tasks that drain the queue.

If the queue fills up, events are dropped (not the order). The match already happened — we just lose the notification. In production: monitor drop rate, alert on sustained drops.

### 1a. Asynchronous Persistence (M4)

`AsyncDatabaseWriter` subscribes to order-state and execution events on its own bounded queue and writes PostgreSQL batches through SQLAlchemy Core/asyncpg. It exposes consumed, written, retry, failed-batch, and persistence-specific drop counters through `/api/v1/metrics/`. A storage adapter protocol keeps the consumer replaceable with a durable broker or recovery journal later.

Batching amortizes SQL parsing, network round trips, transaction commits, and PostgreSQL WAL/fsync work across many events. A synchronous insert in `submit_order()` would make acknowledgement tail latency depend on pool waits, WAL flush policy, disk jitter, replication, and database outages; that is catastrophic for a matching hot path. Shutdown stops event producers first, then flushes the writer's admitted queue.

### 2. Single-Threaded Engine

Real exchange matching engines process one order at a time per partition (instrument group). This eliminates all locking overhead inside the matching loop. Our Python engine follows the same model: one asyncio task serialises all order submissions.

In C++: one CPU core pinned exclusively to the engine thread. No context switches. The OS scheduler never interrupts it.

### 2a. Isolated Strategy Runtime (M5)

`StrategyRuntime` consumes ordered book and trade events outside matching and exposes a narrow `OrderGateway` protocol backed by `OrderService`. A strategy can propose orders, but it cannot reach an order book directly: maximum order size, inventory/notional limits, ownership attribution, and the kill switch are enforced before submission. A failing callback stops and cancels only that strategy's working orders.

`SimpleMarketMaker` now performs cancel/replace quoting on meaningful market moves, quotes both sides of an inventory-adjusted reservation price, and refreshes quotes after fills. The runtime accounts for positions and average-cost realised/unrealised PnL. Its synchronous `replay()` entry point processes the same ordered domain events without wall-clock scheduling, because reproducible backtests and incident reconstruction depend on deterministic event order.

### 3. Pure Computation Boundary

`engine/` has zero dependency on FastAPI, SQLAlchemy, or asyncio. Its interface is:

```python
engine.submit_order(order: Order) → MatchResult
engine.cancel_order(order_id: str) → Order | None
engine.get_snapshot(symbol: str)   → BookSnapshot | None
```

This boundary means the engine can be replaced with a C extension (`ctypes`), a subprocess communicating via shared memory, or an FPGA driver — without touching any API code.

### 4. Price-Time Priority (FIFO)

Standard algorithm for equity and futures exchanges (NYSE, NASDAQ, CME):

1. **Best price first**: highest bid / lowest ask matches first
2. **Time priority at same price**: earlier order fills before later order

Data structure:
- `SortedDict[Price → PriceLevel]` (bids descending, asks ascending)
- `PriceLevel` = `deque[Order]` (FIFO queue per price point)
- `cancel_map: {order_id → (side, price)}` for O(1) cancel lookup

In C++: `std::map<Price, std::list<Order*>>` with custom allocator.
In FPGA: BRAM-backed price-level array, parallel comparators for best-price lookup.

### 5. In-Process Event Bus (No Kafka)

External brokers (Kafka, Redis Pub/Sub) add microsecond-to-millisecond latency from serialisation + network round-trips. For intra-process fan-out, `asyncio.Queue` costs ~100 nanoseconds per put.

Only use external brokers when you need:
- Multi-process / multi-machine fan-out
- Durable event log (replay)
- Independent scaling of producers and consumers

Our simulator is single-process, so we use in-process queues. If you added a second API pod, you'd wire in Redis Pub/Sub at the `broadcast_to_symbol` layer.

### 6. Nanosecond Timestamps for Latency Profiling

Each `Order` carries multiple timestamp checkpoints:

```
created_at_ns    client creates the order object
received_at_ns   engine receives it (gateway latency = received - created)
processed_at_ns  matching complete (processing latency = processed - received)
acked_at_ns      ack dispatched (roundtrip = acked - created)
```

Processing latency (the number HFT engineers obsess over):
- Python simulator: ~2–15 µs typical, ~100 µs p999
- C++ equivalent:   ~100–500 ns typical
- FPGA:             ~50–200 ns typical

The latency dashboard visualises these to help you understand where time goes.

---

## HFT Concepts Explained

### Central Limit Order Book (CLOB)
The canonical market structure for equities and futures. All participants submit orders to a centralised book. Matching is deterministic and transparent. Alternative: dark pools, RFQ markets, OTC.

### Maker vs Taker
- **Maker**: resting order that provides liquidity. Earns a rebate on most venues.
- **Taker**: aggressive order that crosses the spread and removes liquidity. Pays a fee.

The `aggressor_side` field on `Trade` identifies the taker. Fee optimisation (maker rebates) is a major P&L lever for HFT firms.

### Bid-Ask Spread
The difference between the best buy price (bid) and the best sell price (ask). Market makers earn the spread. Takers pay half the spread on each side. Spread = transaction cost for non-market-makers.

### Geometric Brownian Motion (GBM)
The price model behind Black-Scholes. Assumes log-returns are normally distributed. Our market data feed uses GBM to generate realistic-looking price paths. Real prices have fatter tails, autocorrelated volatility, and intraday patterns — but GBM is a good starting approximation.

### Order Types
- **LIMIT**: maximum price to pay (buy) or minimum to accept (sell). Rests in book if not immediately crossable.
- **MARKET**: fill immediately at whatever price is available. No price guarantee.
- **IOC** (Immediate or Cancel): fill what's available right now, cancel remainder.
- **FOK** (Fill or Kill): fill the entire quantity atomically or reject entirely.

---

## Quick Start

### Local (no Docker)

```bash
# 1. Clone and enter
git clone https://github.com/georgexu1118-ctrl/mini-hft-system.git
cd mini-hft-system

# 2. Create virtual environment
python -m venv .venv
source .venv/bin/activate        # Linux/Mac
.venv\Scripts\activate           # Windows

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env
# Edit .env if needed (DATABASE_URL etc.)

# 5. Run the API (no DB required for in-memory mode)
uvicorn api.main:app --reload

# 6. Explore
open http://localhost:8000/docs   # Swagger UI
```

### With Docker

```bash
docker-compose up --build
```

This starts the API on :8000 and PostgreSQL on :5432.

### Run Tests

```bash
pytest tests/ -v
```

---

## Project Structure

```
mini-hft-system/
│
├── engine/                     # Core trading engine — pure Python, no I/O
│   ├── core/
│   │   ├── types.py            # Enums and type aliases
│   │   ├── order.py            # Order + Trade dataclasses (slots=True)
│   │   ├── order_book.py       # PriceLevel, OrderBook, BookSnapshot
│   │   └── matching_engine.py  # MatchingEngine (the hot path)
│   ├── market_data/
│   │   ├── feed.py             # GBM price generator
│   │   └── publisher.py        # Async background task: tick → TickerEvent
│   └── risk/
│       └── limits.py           # Pre-trade risk checks
│
├── api/                        # FastAPI application
│   ├── main.py                 # App factory, lifespan, background tasks
│   ├── config.py               # Re-exports settings
│   ├── dependencies.py         # FastAPI DI functions
│   ├── routers/
│   │   ├── orders.py           # POST/DELETE/GET /orders
│   │   ├── market_data.py      # Snapshot REST + WebSocket endpoint
│   │   └── metrics.py          # Latency and engine stats
│   ├── websocket/
│   │   ├── manager.py          # Connection + subscription management
│   │   └── schemas.py          # Pydantic WS message models
│   └── services/
│       ├── order_service.py    # Engine↔EventBus bridge
│       └── metrics_service.py  # Stats aggregation
│
├── shared/                     # Cross-package domain types
│   ├── events.py               # DomainEvent subclasses + EventBus
│   └── constants.py            # System-wide constants
│
├── strategy/                   # Strategy framework
│   ├── base.py                 # Abstract Strategy base class
│   ├── risk.py                 # Per-strategy pre-trade controls
│   ├── runtime.py              # Event-driven executor, PnL, kill switch/replay
│   └── examples/
│       └── market_maker.py     # Simple symmetric MM strategy
│
├── infra/                      # Infrastructure configuration
│   ├── config/
│   │   └── settings.py         # Pydantic-settings (reads .env)
│   └── db/
│       ├── database.py         # SQLAlchemy async Core engine
│       ├── schema.py           # Core table definitions
│       ├── serialization.py    # Domain event to row translation
│       ├── writer.py           # Bounded async batch persistence service
│       └── migrations/
│           └── 001_initial.sql # Initial schema (orders, trades, views)
│
├── tests/
│   └── engine/
│       ├── test_matching_engine.py  # Full engine test suite (30+ tests)
│       └── test_order_book.py       # OrderBook unit tests
│
├── frontend/                   # Next.js app (Milestone 6)
│   └── .gitkeep
│
├── docker-compose.yml          # API + PostgreSQL
├── Dockerfile
├── requirements.txt
└── pyproject.toml
```

---

## API Reference

### Orders

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/v1/orders/` | Submit a new order |
| `DELETE` | `/api/v1/orders/{id}` | Cancel a resting order |
| `GET` | `/api/v1/orders/{id}` | Get order by ID |

**Submit order example:**
```bash
curl -X POST http://localhost:8000/api/v1/orders/ \
  -H "Content-Type: application/json" \
  -d '{
    "symbol": "AAPL",
    "side": "BUY",
    "order_type": "LIMIT",
    "quantity": 100,
    "price": 150.25,
    "time_in_force": "GTC"
  }'
```

**Response includes latency breakdown:**
```json
{
  "order_id": "...",
  "status": "OPEN",
  "filled_quantity": 0,
  "trades": [],
  "gateway_latency_us": 12.4,
  "processing_latency_us": 3.1,
  "roundtrip_latency_us": 28.7
}
```

### Market Data

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/market-data/symbols` | List symbols |
| `GET` | `/api/v1/market-data/{symbol}/snapshot` | Point-in-time book |
| `WS` | `/api/v1/market-data/ws` | Real-time stream |

### Metrics

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/metrics/` | Full engine stats |
| `GET` | `/api/v1/metrics/latency` | Latency percentiles |
| `GET` | `/api/v1/metrics/ws` | WS connection stats |

---

## WebSocket Protocol

Connect to `ws://localhost:8000/api/v1/market-data/ws`

**Subscribe to a symbol:**
```json
{"action": "subscribe", "symbol": "AAPL"}
```

**Subscribe to everything:**
```json
{"action": "subscribe_all"}
```

**Server messages (discriminate on `type` field):**

| `type` | Description |
|--------|-------------|
| `BOOK_UPDATE` | Full depth snapshot after every book change |
| `TRADE` | Public trade print |
| `ORDER_ACK` | Order status update (ack, fill, cancel) |
| `TICKER` | Synthetic bid/ask/last from market data feed |
| `LATENCY_SNAPSHOT` | p50/p99/p999 pushed every 5 seconds |
| `HEARTBEAT` | Keep-alive, every 30 seconds |
| `ERROR` | Server-side error |

---

## Implementation Roadmap

| Milestone | Focus | Status |
|-----------|-------|--------|
| **M1** | Core matching engine + tests | ✅ Done |
| **M2** | FastAPI backend + WebSocket | ✅ Done |
| **M3** | Market data feed (GBM) | ✅ Done |
| **M4** | PostgreSQL persistence (async writer) | Done |
| **M5** | Strategy sandbox + executor | Done |
| **M6** | Next.js dashboard (order book ladder, latency charts) | 🔜 Planned |
| **M7** | C++ matching engine (Python binding via ctypes/pybind11) | 🔜 Future |
| **M8** | FPGA research notes + simulation | 🔜 Future |

---

## Future: C++ / FPGA Path

The `engine/` boundary is designed for this upgrade:

**C++ step (M7):**
```python
# Replace engine/core/matching_engine.py with:
import ctypes
lib = ctypes.CDLL("./engine_core.so")

class MatchingEngine:
    def submit_order(self, order: Order) -> MatchResult:
        # Marshal to C struct, call lib.submit_order(), unmarshal result
        ...
```

The API layer doesn't change at all — it still calls `engine.submit_order(order)`.

**FPGA step (M8):**
The FPGA replaces the matching loop. The CPU handles everything else (risk, persistence, WS). Communication via shared memory (DPDK, RDMA) or PCIe DMA.

Key FPGA concepts to explore:
- Price level lookup as BRAM-indexed arrays (O(1) by tick index)
- Parallel comparators for best-price detection
- Hard-coded FIFO queues per price level
- Pipeline: receive order → match → emit trade in < 200 ns

---

## Tech Stack

| Layer | Technology | Why |
|-------|-----------|-----|
| Matching engine | Python 3.11 (→ C++) | Pure computation, zero I/O, easy to replace |
| API | FastAPI | Async-native, auto-docs, Pydantic validation |
| Real-time | WebSockets (native FastAPI) | Zero round-trip for push; no polling |
| Database | PostgreSQL + SQLAlchemy Core/asyncpg | Batched append-only trade log off the hot path |
| Config | Pydantic-Settings | Type-safe env var parsing |
| Frontend | Next.js 16 + TypeScript (M6) | React server components, real-time charts |
| Deployment | Docker Compose → Vercel/Supabase | Dev parity, one-command deploy |

---

## Contributing

Built as a learning project. PRs welcome for:
- Additional order types (Stop, Stop-Limit, Iceberg)
- Frontend dashboard (M6)
- C++ engine binding (M7)
