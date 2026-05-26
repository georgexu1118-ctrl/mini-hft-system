"""
Comparable HAL-level latency benchmark for the Python and C++ engines.

The object scenarios time `MatchingHAL.submit_order(Order)`: this intentionally
includes native frame conversion and domain-result hydration because it is the
latency experienced by the current service layer. The `frame_*` scenarios time
pre-encoded binary submission and result framing, exposing the replaceable
transport boundary without Python trade/snapshot projection. Order construction
and book seeding are outside every timer. Results, not assumptions, should
justify later node-pool or binary-service optimizations.
"""
from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
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


def _result(backend: str, scenario: str, samples_ns: list[int]) -> BenchResult:
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
    scenario = "frame_resting" if frames else "resting_no_fill"
    return _result(backend, scenario, samples)


def bench_single_fill(
    backend: str, factory: Factory, rounds: int, *, frames: bool = False
) -> BenchResult:
    samples: list[int] = []
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
    scenario = "frame_single_fill" if frames else "single_fill"
    return _result(backend, scenario, samples)


def bench_sweep(
    backend: str, factory: Factory, rounds: int, depth: int, *, frames: bool = False
) -> BenchResult:
    samples: list[int] = []
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
    scenario = f"frame_sweep_{depth}" if frames else f"market_sweep_{depth}"
    return _result(backend, scenario, samples)


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
    print("backend  scenario              p50_us    p99_us   p999_us    throughput")
    print("-------  --------------------  --------  --------  --------  ------------")
    for value in results:
        print(
            f"{value.backend:7s}  {value.scenario:20s}  "
            f"{value.p50_us:8.2f}  {value.p99_us:8.2f}  {value.p999_us:8.2f}  "
            f"{value.throughput_ops:12,.0f}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Python/C++ MatchingHAL benchmark")
    parser.add_argument("--rounds", type=int, default=2_000)
    parser.add_argument("--warmup", type=int, default=100)
    parser.add_argument("--backend", choices=("both", "python", "cpp"), default="both")
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


if __name__ == "__main__":
    main()
