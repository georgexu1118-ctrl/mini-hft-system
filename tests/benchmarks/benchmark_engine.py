"""
Comparable HAL-level latency benchmark for the Python and C++ engines.

The object scenarios time `MatchingHAL.submit_order(Order)`: this intentionally
includes native frame conversion and domain-result hydration because it is the
latency experienced by the current service layer. The `frame_*` scenarios time
pre-encoded binary submission and result framing, exposing the replaceable
transport boundary without Python trade/snapshot projection. Order construction
and book seeding are outside every timer. Sustained throughput is measured in a
separate submission-only pass with one wall-clock timer. Results, not
assumptions, should justify later node-pool or binary-service optimizations.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

# Keep the documented direct-script invocation usable from the repository root.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from engine.core.order import Order  # noqa: E402
from engine.core.types import OrderSide, OrderType  # noqa: E402
from engine.hal.abstract import MatchingHAL  # noqa: E402
from engine.hal.cpp_hal import CppMatchingHAL, is_available  # noqa: E402
from engine.hal.software import SoftwareMatchingHAL  # noqa: E402

Factory = Callable[[], MatchingHAL]


@dataclass(frozen=True, slots=True)
class BenchResult:
    backend: str
    scenario: str
    rounds: int
    p50_us: float
    p99_us: float
    p999_us: float
    throughput_ops: float
    wall_throughput_ops: float


def _order(
    side: OrderSide,
    price: float | None,
    quantity: int = 100,
    order_type: OrderType = OrderType.LIMIT,
) -> Order:
    return Order(
        symbol="BENCH",
        side=side,
        order_type=order_type,
        quantity=quantity,
        price=price,
    )


def _engine(factory: Factory) -> MatchingHAL:
    engine = factory()
    engine.register_symbol("BENCH")
    return engine


def _result(
    backend: str,
    scenario: str,
    samples_ns: list[int],
    wall_elapsed_ns: int,
) -> BenchResult:
    values = sorted(value / 1_000 for value in samples_ns)
    rounds = len(values)
    elapsed_s = sum(samples_ns) / 1_000_000_000

    def percentile(ratio: float) -> float:
        return values[min(int(rounds * ratio), rounds - 1)]

    return BenchResult(
        backend=backend,
        scenario=scenario,
        rounds=rounds,
        p50_us=percentile(0.50),
        p99_us=percentile(0.99),
        p999_us=percentile(0.999),
        throughput_ops=rounds / elapsed_s,
        wall_throughput_ops=rounds / (wall_elapsed_ns / 1_000_000_000),
    )


def bench_resting(
    backend: str, factory: Factory, rounds: int, *, frames: bool = False
) -> BenchResult:
    engine = _engine(factory)
    orders = [_order(OrderSide.BUY, 100.0 - index * 0.001) for index in range(rounds)]
    encoded = [order.to_bytes() for order in orders] if frames else []
    samples: list[int] = []
    for index, order in enumerate(orders):
        start = time.perf_counter_ns()
        if frames:
            engine.submit_order_bytes(encoded[index])
        else:
            engine.submit_order(order)
        samples.append(time.perf_counter_ns() - start)

    throughput_engine = _engine(factory)
    wall_start = time.perf_counter_ns()
    if frames:
        for frame in encoded:
            throughput_engine.submit_order_bytes(frame)
    else:
        for order in orders:
            throughput_engine.submit_order(order)
    wall_elapsed_ns = time.perf_counter_ns() - wall_start
    scenario = "frame_resting" if frames else "resting_no_fill"
    return _result(backend, scenario, samples, wall_elapsed_ns)


def bench_single_fill(
    backend: str, factory: Factory, rounds: int, *, frames: bool = False
) -> BenchResult:
    samples: list[int] = []
    throughput_cases: list[tuple[MatchingHAL, Order, bytes]] = []
    for _ in range(rounds):
        engine = _engine(factory)
        engine.submit_order(_order(OrderSide.BUY, 150.0))
        taker = _order(OrderSide.SELL, 150.0)
        encoded = taker.to_bytes() if frames else b""
        start = time.perf_counter_ns()
        if frames:
            engine.submit_order_bytes(encoded)
        else:
            engine.submit_order(taker)
        samples.append(time.perf_counter_ns() - start)
        throughput_engine = _engine(factory)
        throughput_engine.submit_order(_order(OrderSide.BUY, 150.0))
        throughput_taker = _order(OrderSide.SELL, 150.0)
        throughput_cases.append(
            (
                throughput_engine,
                throughput_taker,
                throughput_taker.to_bytes() if frames else b"",
            )
        )

    wall_start = time.perf_counter_ns()
    if frames:
        for throughput_engine, _, throughput_frame in throughput_cases:
            throughput_engine.submit_order_bytes(throughput_frame)
    else:
        for throughput_engine, throughput_taker, _ in throughput_cases:
            throughput_engine.submit_order(throughput_taker)
    wall_elapsed_ns = time.perf_counter_ns() - wall_start
    scenario = "frame_single_fill" if frames else "single_fill"
    return _result(backend, scenario, samples, wall_elapsed_ns)


def bench_sweep(
    backend: str, factory: Factory, rounds: int, depth: int, *, frames: bool = False
) -> BenchResult:
    samples: list[int] = []
    throughput_cases: list[tuple[MatchingHAL, Order, bytes]] = []
    for _ in range(rounds):
        engine = _engine(factory)
        for level in range(depth):
            engine.submit_order(_order(OrderSide.SELL, 100.0 + level * 0.01, 10))
        taker = _order(OrderSide.BUY, None, depth * 10, OrderType.MARKET)
        encoded = taker.to_bytes() if frames else b""
        start = time.perf_counter_ns()
        if frames:
            engine.submit_order_bytes(encoded)
        else:
            engine.submit_order(taker)
        samples.append(time.perf_counter_ns() - start)
        throughput_engine = _engine(factory)
        for level in range(depth):
            throughput_engine.submit_order(
                _order(OrderSide.SELL, 100.0 + level * 0.01, 10)
            )
        throughput_taker = _order(OrderSide.BUY, None, depth * 10, OrderType.MARKET)
        throughput_cases.append(
            (
                throughput_engine,
                throughput_taker,
                throughput_taker.to_bytes() if frames else b"",
            )
        )

    wall_start = time.perf_counter_ns()
    if frames:
        for throughput_engine, _, throughput_frame in throughput_cases:
            throughput_engine.submit_order_bytes(throughput_frame)
    else:
        for throughput_engine, throughput_taker, _ in throughput_cases:
            throughput_engine.submit_order(throughput_taker)
    wall_elapsed_ns = time.perf_counter_ns() - wall_start
    scenario = f"frame_sweep_{depth}" if frames else f"market_sweep_{depth}"
    return _result(backend, scenario, samples, wall_elapsed_ns)


def run_backend(backend: str, factory: Factory, rounds: int) -> list[BenchResult]:
    return [
        bench_resting(backend, factory, rounds),
        bench_single_fill(backend, factory, rounds),
        bench_sweep(backend, factory, rounds, 10),
        bench_resting(backend, factory, rounds, frames=True),
        bench_single_fill(backend, factory, rounds, frames=True),
        bench_sweep(backend, factory, rounds, 10, frames=True),
    ]


def report(results: list[BenchResult]) -> None:
    print(
        "backend  scenario              p50_us    p99_us   p999_us  "
        "timed_ops/s    wall_ops/s"
    )
    print(
        "-------  --------------------  --------  --------  --------  "
        "------------  ------------"
    )
    for value in results:
        print(
            f"{value.backend:7s}  {value.scenario:20s}  "
            f"{value.p50_us:8.2f}  {value.p99_us:8.2f}  {value.p999_us:8.2f}  "
            f"{value.throughput_ops:12,.0f}  {value.wall_throughput_ops:12,.0f}"
        )


def _command_output(*command: str) -> str | None:
    try:
        return subprocess.check_output(
            command,
            cwd=Path(__file__).resolve().parents[2],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _cpu_name() -> str:
    if sys.platform != "win32":
        return platform.processor() or "unknown"
    try:
        import winreg

        key_path = r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
            return str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
    except OSError:
        return platform.processor() or "unknown"


def write_json_report(
    output: Path,
    results: list[BenchResult],
    rounds: int,
    warmup: int,
) -> None:
    git_status = _command_output("git", "status", "--porcelain")
    report_data = {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": _command_output("git", "rev-parse", "HEAD"),
        "git_worktree_dirty": bool(git_status),
        "benchmark": {
            "rounds": rounds,
            "warmup": warmup,
            "timer": "time.perf_counter_ns",
            "timed_ops_per_second": "rounds divided by summed per-call duration",
            "wall_ops_per_second": (
                "rounds divided by a separate wall-clock submission pass; order "
                "construction, encoding, engine creation, and book seeding are excluded"
            ),
        },
        "environment": {
            "cpu": _cpu_name(),
            "os": platform.platform(),
            "architecture": platform.machine(),
            "python": platform.python_version(),
            "python_implementation": platform.python_implementation(),
            "compiler": os.environ.get(
                "CXX",
                (
                    "MSVC /O2 /std:c++20"
                    if sys.platform == "win32"
                    else "C++20 -O3 -march=native"
                ),
            ),
        },
        "results": [asdict(value) for value in results],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report_data, indent=2) + "\n", encoding="utf-8")


def verify_claim(
    results: list[BenchResult],
    max_p50_us: float,
    min_wall_throughput: float,
) -> None:
    result = next(
        (
            value
            for value in results
            if value.backend == "cpp" and value.scenario == "frame_resting"
        ),
        None,
    )
    if result is None:
        raise SystemExit("claim verification requires the C++ frame_resting benchmark")
    failures: list[str] = []
    if result.p50_us > max_p50_us:
        failures.append(f"p50 {result.p50_us:.2f} us exceeds {max_p50_us:.2f} us")
    if result.wall_throughput_ops < min_wall_throughput:
        failures.append(
            f"wall throughput {result.wall_throughput_ops:,.0f} ops/s is below "
            f"{min_wall_throughput:,.0f} ops/s"
        )
    if failures:
        raise SystemExit("claim verification failed: " + "; ".join(failures))
    print(
        "claim verified: "
        f"p50={result.p50_us:.2f} us, "
        f"wall throughput={result.wall_throughput_ops:,.0f} orders/s"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Python/C++ MatchingHAL benchmark")
    parser.add_argument("--rounds", type=int, default=2_000)
    parser.add_argument("--warmup", type=int, default=100)
    parser.add_argument("--backend", choices=("both", "python", "cpp"), default="both")
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--verify-claim", action="store_true")
    parser.add_argument("--claim-max-p50-us", type=float, default=1.6)
    parser.add_argument("--claim-min-wall-throughput", type=float, default=542_000)
    args = parser.parse_args()

    factories: list[tuple[str, Factory]] = []
    if args.backend in ("both", "python"):
        factories.append(("python", SoftwareMatchingHAL))
    if args.backend in ("both", "cpp"):
        if not is_available():
            raise SystemExit("C++ backend requested but hft_engine_cpp is not built")
        factories.append(("cpp", CppMatchingHAL))

    for backend, factory in factories:
        run_backend(backend, factory, args.warmup)

    results: list[BenchResult] = []
    for backend, factory in factories:
        results.extend(run_backend(backend, factory, args.rounds))
    report(results)
    if args.json_output:
        write_json_report(args.json_output, results, args.rounds, args.warmup)
    if args.verify_claim:
        verify_claim(
            results,
            max_p50_us=args.claim_max_p50_us,
            min_wall_throughput=args.claim_min_wall_throughput,
        )


if __name__ == "__main__":
    main()
