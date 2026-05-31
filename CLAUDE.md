# Claude Code Guide - Mini HFT System

This repository is a low-latency quantitative trading simulator with a CLOB
matching engine, FastAPI backend, Next.js dashboard, async persistence,
strategy runtime, and C++/FPGA preparation layers.

Claude-specific workspace files belong in `.claude/`. Codex-specific project
memory belongs in `.codex/AGENTS.md`. Keep this file focused on Claude Code
behavior and architectural judgment.

## Repository Understanding

- `engine/core/` is the latency-sensitive matching domain.
- `engine/hal/` is the backend seam for Python, C++, and future FPGA matching.
- `api/services/` bridges synchronous matching to async infrastructure.
- `shared/events.py` fans out domain events on bounded in-process queues.
- `infra/db/` persists cold-path execution events in batches.
- `strategy/` runs downstream of the EventBus and never inside matching.
- `frontend/` visualizes orders, depth, trades, strategy metrics, latency, and
  health.
- `docs/fpga_acceleration_design.md` describes the intended hardware path.

## Development Philosophy

Work as if this is production trading infrastructure. Correctness and latency
discipline matter more than broad refactors. Prefer explicit, boring code in
hot paths and richer abstractions only on cold paths where they buy clarity.

Read existing tests and local patterns before changing code. Preserve public
API behavior, WebSocket message shape, order lifecycle semantics, and benchmark
characteristics unless the task explicitly requires a change.

## Latency-Aware Coding Standards

- Do not add I/O, logging, network calls, database calls, sleeps, locks, or
  `await` inside `engine/core/`.
- Do not import FastAPI, SQLAlchemy, asyncio service code, or frontend code from
  `engine/`.
- Use `time.time_ns()` for engine/event timestamps.
- Use `@dataclass(slots=True)` for per-order/per-trade hot-path objects.
- Avoid `**kwargs`, dynamic dict construction, variable-length payloads, and
  avoidable allocations in matching, binary codec, ring buffer, and HAL code.
- Keep fixed-size binary frames for process/hardware boundaries.
- Use bounded queues for cold-path consumers; never let persistence or
  WebSocket fan-out block order acknowledgement.
- Benchmark before and after modifying matching logic or wire/ring-buffer code.

## Architectural Guidance

- Services must depend on `MatchingHAL`, not concrete `MatchingEngine`.
- Routers should stay thin and delegate to services.
- Strategies submit through `OrderGateway` and cannot reach order books
  directly.
- Persistence remains asynchronous, batched, idempotent, and outside the engine
  callback except for non-blocking publication.
- Book snapshots/deltas must remain sequence-aware so clients can recover from
  missed updates.
- C++ and FPGA changes must conform to the HAL and binary protocol rather than
  creating alternate API paths.

## Testing Requirements

- Run `pytest tests/ -v` after backend changes when practical.
- Run focused suites for touched areas: `tests/engine`, `tests/api`,
  `tests/infra`, or `tests/strategy`.
- Run `python tests/benchmarks/benchmark_engine.py --rounds 2000` after
  matching, binary protocol, ring buffer, or HAL changes.
- For frontend changes, run `npm run type-check` and `npm run build` from
  `frontend/`.
- Add or update tests for behavior changes, new interfaces, and HAL
  conformance.

## Refactoring Rules

- Do not refactor hot-path modules as a drive-by improvement.
- Do not change matching semantics while moving code.
- Do not introduce a dependency that belongs to the cold path into the engine.
- Keep file and module ownership clear; split only when it reduces real
  complexity or matches existing boundaries.
- Preserve existing milestones, docs, and tests unless migrating them into the
  correct agent-specific location.

## Trading-System Safety Requirements

- Preserve price-time priority and maker-price execution.
- Preserve TIF behavior for GTC/GTD/IOC/FOK and market-order non-resting.
- Preserve risk checks and strategy kill-switch behavior.
- Do not bypass strategy ownership accounting.
- Do not make live trading assumptions; the project is a simulator with future
  paper/live adapters on the roadmap.
- Treat drop counters, failed persistence batches, callback failures, and
  degraded health as operational signals, not cosmetic metrics.

For the full project architecture, workflows, roadmap, and component inventory,
read `.codex/AGENTS.md`.
