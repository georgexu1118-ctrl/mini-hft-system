# Multi-Agent Migration Report

Date: 2026-05-31
Repository: `georgexu1118-ctrl/mini-hft-system`

## Repository Summary

Mini HFT System is a low-latency trading simulator built around a Python CLOB
matching engine, FastAPI service layer, bounded in-process event bus, async
PostgreSQL persistence, strategy runtime, WebSocket market-data fan-out, and a
Next.js dashboard. The project is explicitly designed as production-style
trading infrastructure with a path toward C++ and FPGA acceleration.

## Detected Architecture

- Matching engine: `engine/core/matching_engine.py` and `order_book.py`.
- Order management: `api/services/order_service.py`, `api/routers/orders.py`.
- Market data: synthetic GBM publisher plus REST/WebSocket book snapshots and
  deltas.
- Strategy framework: `StrategyRuntime`, `BaseStrategy`, `StrategyRiskGuard`,
  and `SimpleMarketMaker`.
- Event system: bounded `EventBus` with domain event dataclasses.
- Database: SQLAlchemy Core async PostgreSQL writer with idempotent upserts and
  batch retry.
- Dashboard/UI: Next.js, React, TypeScript, Tailwind, Recharts.
- APIs: FastAPI routers for orders, market data, metrics, and strategies.
- WebSockets: connection manager, schema models, heartbeat, latency, PnL, and
  system-health broadcasts.
- Latency monitoring: per-order nanosecond checkpoints, engine stats, metrics
  service, dashboard latency chart.
- Simulation/paper trading: synthetic feed, strategy runtime, replay support.
  No broker-backed paper-trading adapter exists yet.
- FPGA/HAL preparation: `MatchingHAL`, software/C++/FPGA HALs, fixed-size
  binary protocol, SPSC ring buffer, C++ scaffold, FPGA design doc.

## Existing Agent Files Found

- Found root `AGENTS.md`.
- No existing `CLAUDE.md` was present.
- No existing `.claude/` directory was present.
- No existing repo-local `.codex/` directory was present.

## Files Created

- `.codex/AGENTS.md`: professional Codex project memory and operating guide.
- `CLAUDE.md`: Claude Code-specific repository guide.
- `.claude/README.md`: Claude workspace boundary note.
- `docs/multi_agent_migration_report.md`: this migration report.

## Files Modified

- `AGENTS.md`: replaced the previous long mixed-agent guide with a short
  compatibility entrypoint that points Codex to `.codex/AGENTS.md` and Claude
  to `CLAUDE.md`.

## Instructions Moved

- Existing architecture rules, hot-path constraints, HAL guidance, binary
  protocol expectations, testing discipline, and FPGA-readiness rules were
  reconstructed into `.codex/AGENTS.md`.
- Claude-specific operating guidance and development philosophy were placed in
  `CLAUDE.md`.
- Duplicated root-level agent instructions were removed by turning root
  `AGENTS.md` into a pointer file.

## Architecture Review Recommendations

### Latency Improvements

- Replace `SortedDict` and Python object-heavy matching with the C++ backend for
  real latency work; retain Python as correctness reference.
- Add benchmark trend tracking and fail builds on material hot-path regression.
- Avoid snapshot generation inside the immediate match path for future high
  throughput; emit compact deltas or defer depth projection when needed.
- Use preallocated trade/result pools in C++ and eventually a zero-copy HAL
  completion path.

### Scalability Improvements

- Partition books by symbol group so each partition has a single-threaded
  matching loop while scaling across cores.
- Add external durable event journal only at cold-path boundaries.
- Track queue depths, drops, and consumer lag per subscriber.
- Add CI/CD and container hardening before production-style deployment.

### Strategy Isolation

- Keep strategies downstream of EventBus and behind `OrderGateway`.
- Add per-strategy rate limits, capital limits, and max loss controls.
- Consider process isolation for untrusted strategy plugins or future strategy
  marketplace work.

### Risk Management

- Add global and per-strategy kill switches exposed through audited admin APIs.
- Add self-trade prevention, max order rate, max open orders, price collars,
  notional caps, and inventory limits.
- Add explicit live-trading safety gates before any broker/exchange adapter.

### Event Processing

- Preserve bounded queues and non-blocking publication.
- Add event journal/replay for recovery and deterministic research.
- Standardize event schemas for future inter-process consumers.

### Monitoring

- Add Prometheus/OpenTelemetry metrics.
- Add structured cold-path logs with order IDs, event IDs, and strategy IDs.
- Track persistence retry latency, failed batches, queue drops, WebSocket
  subscribers, and strategy callback failures.
- Surface HAL backend health and version in `/health`.

### Hardware Acceleration Readiness

- Keep `MatchingHAL` as the only service-to-engine boundary.
- Keep binary frames fixed-size and cache-line aligned.
- Add conformance tests for C++ and FPGA HAL behavior against the Python
  reference engine.
- Define hardware counters, DMA ring telemetry, and bitstream compatibility
  checks before implementing real FPGA integration.

## Remaining Technical Debt

- No CI/CD workflows are currently present.
- C++ backend is scaffolded but not production-integrated.
- FPGA HAL is scaffold-only and raises until hardware support exists.
- No real exchange/broker paper-trading adapter exists.
- No durable event journal exists for replay/recovery beyond in-memory events
  and database persistence.
- Runtime metrics exist but are not exported to a standard observability system.
- Existing docs contain some milestone language that can be further normalized
  as future work.

## FPGA-Readiness Assessment

The repository is unusually FPGA-aware for a simulator. Strengths include the
HAL seam, fixed-size binary protocol, ring buffer abstraction, clear DMA design
documentation, and separation of hot path from I/O. The remaining gap is
implementation: real hugepage allocation, PCIe/XRT integration, FPGA bitstream,
hardware counters, host-side order cache, and conformance/latency tests are not
yet built.

Assessment: strong architectural readiness, scaffold-stage implementation.

## Overall Project Maturity Assessment

The project is beyond a toy prototype: it has coherent architecture, tests,
dashboard functionality, persistence, strategy isolation, and explicit
performance doctrine. It is not production trading infrastructure yet because
CI/CD, operational hardening, durable event recovery, production risk controls,
and live/paper trading adapters are not complete.

Maturity: advanced educational/prototype infrastructure with a credible path to
production-style components.
