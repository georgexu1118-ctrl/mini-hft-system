# Matching Engine Performance

The project has two behaviorally compatible matching backends:

- A reference Python price-time-priority CLOB.
- A C++20 CLOB exposed to Python through pybind11 and a fixed 64-byte binary
  `ORDER_FRAME` interface.

The headline latency and throughput numbers refer to the optimized C++ binary
frame path. They do not describe the Python reference backend or the object API,
which also reconstructs Python orders, trades, snapshots, and domain events.

## Reproduce the Claim

Build the native extension and run the claim verifier from the repository root:

```powershell
uv sync --extra dev --extra cpp
engine\cpp\build_windows.bat
.venv\Scripts\python.exe tests\benchmarks\benchmark_engine.py `
  --backend cpp `
  --rounds 5000 `
  --warmup 500 `
  --verify-claim `
  --json-output docs\benchmarks\local.json
```

The verifier requires the C++ `frame_resting` scenario to meet both thresholds:

- p50 latency no greater than `1.6 microseconds`.
- wall-clock throughput of at least `542,000 orders/second`.

The wall-clock metric uses one timer around the complete measured submission
loop. Order construction and binary encoding happen before that timer. Each
submission crosses the Python-to-pybind11 boundary, decodes one fixed-size order
frame, executes the C++20 matching path, and returns a fixed-size result frame.

Results vary with CPU, compiler, power policy, thermal state, and background
load. A failed claim verification on slower hardware does not indicate a
correctness failure; it means that environment did not reproduce the stated
performance threshold.

## Recorded Evidence

Machine-readable benchmark reports live in `docs/benchmarks/`. Each report
records the source commit, worktree state, CPU, OS, Python version, compiler
settings, warmup count, sample count, latency percentiles, reciprocal timed-call
throughput, and wall-clock scenario throughput. Published evidence should come
from a report with `git_worktree_dirty` set to `false`.

The C++ and Python implementations are checked for matching semantics in
`tests/engine/test_cpp_hal.py`. Performance results are meaningful only when the
correctness suite passes.
