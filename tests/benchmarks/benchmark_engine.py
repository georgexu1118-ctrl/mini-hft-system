"""
Matching engine benchmark suite.

Measures hot-path latency for the Python CLOB engine. These numbers establish
the baseline that C++ (M7) and FPGA (M8) implementations must improve on.

Usage:
    python tests/benchmarks/benchmark_engine.py
    python tests/benchmarks/benchmark_engine.py --rounds 5000

Expected results (developer laptop, Python 3.11):
  Resting order (no fill):    p50 ~300 µs,  p99 ~500 µs,  p999 ~1 ms
  1-fill match:               p50 ~600 µs,  p99 ~900 µs,  p999 ~2 ms
  Sweep (10 levels, 10 fills):p50 ~1.5 ms,  p99 ~3 ms

  Throughput (resting):       ~3,000–5,000 orders/sec

  Root cause: Python object allocation in MatchResult/Trade + GC pressure.
  C++ arena allocator + pre-allocated Trade pool would reach ~1 µs p99.
"""
from __future__ import annotations

import argparse
import statistics
import time
from typing import NamedTuple

from engine.core.matching_engine import MatchingEngine
from engine.core.order import Order
from engine.core.types import OrderSide, OrderType, TimeInForce


# ── Scenarios ─────────────────────────────────────────────────────────────────

class BenchResult(NamedTuple):
    name: str
    rounds: int
    p50_us: float
    p99_us: float
    p999_us: float
    throughput_ops: float


def _make_order(
    side: OrderSide,
    price: float,
    qty: int = 100,
    otype: OrderType = OrderType.LIMIT,
    tif: TimeInForce = TimeInForce.GTC,
) -> Order:
    return Order(
        symbol="BENCH",
        side=side,
        order_type=otype,
        quantity=qty,
        price=price,
        time_in_force=tif,
    )


def bench_resting_orders(rounds: int) -> BenchResult:
    """
    Resting limit orders: no match, just insert into the book.
    Isolates PriceLevel.add() + SortedDict insertion cost.
    """
    engine = MatchingEngine()
    engine.register_symbol("BENCH")

    latencies: list[float] = []
    price = 100.0
    for _ in range(rounds):
        order = _make_order(OrderSide.BUY, price)
        price -= 0.01  # unique prices → new PriceLevel each time
        t0 = time.perf_counter_ns()
        engine.submit_order(order)
        t1 = time.perf_counter_ns()
        latencies.append((t1 - t0) / 1_000)  # ns → µs

    return _stats("resting_no_fill", rounds, latencies)


def bench_single_fill(rounds: int) -> BenchResult:
    """
    Submit buy at X, then sell at X → 1-fill match.
    Measures full match loop: cross check + Trade allocation + status update.
    """
    latencies: list[float] = []
    for _ in range(rounds):
        engine = MatchingEngine()
        engine.register_symbol("BENCH")
        # Pre-place a resting buy
        buy = _make_order(OrderSide.BUY, 150.0)
        engine.submit_order(buy)

        # Now time the crossing sell
        sell = _make_order(OrderSide.SELL, 150.0)
        t0 = time.perf_counter_ns()
        engine.submit_order(sell)
        t1 = time.perf_counter_ns()
        latencies.append((t1 - t0) / 1_000)

    return _stats("single_fill", rounds, latencies)


def bench_market_sweep(rounds: int, depth: int = 10) -> BenchResult:
    """
    Market order sweeping N price levels.
    Tests the inner matching loop over multiple PriceLevels.
    """
    latencies: list[float] = []
    for _ in range(rounds):
        engine = MatchingEngine()
        engine.register_symbol("BENCH")
        # Place resting sells at 10 distinct prices
        for i in range(depth):
            engine.submit_order(_make_order(OrderSide.SELL, 100.0 + i * 0.01, qty=10))

        # Market buy sweeps all of them
        mkt = _make_order(OrderSide.BUY, None, qty=depth * 10, otype=OrderType.MARKET)  # type: ignore[arg-type]
        t0 = time.perf_counter_ns()
        engine.submit_order(mkt)
        t1 = time.perf_counter_ns()
        latencies.append((t1 - t0) / 1_000)

    return _stats(f"market_sweep_{depth}_levels", rounds, latencies)


def bench_throughput(rounds: int) -> float:
    """Orders per second (resting, pre-allocated engines per batch)."""
    engine = MatchingEngine()
    engine.register_symbol("BENCH")
    orders = [_make_order(OrderSide.BUY, 100.0 - i * 0.001) for i in range(rounds)]

    t0 = time.perf_counter_ns()
    for o in orders:
        engine.submit_order(o)
    elapsed_s = (time.perf_counter_ns() - t0) / 1e9

    return rounds / elapsed_s


def _stats(name: str, rounds: int, latencies: list[float]) -> BenchResult:
    s = sorted(latencies)
    n = len(s)
    p50  = s[int(n * 0.50)]
    p99  = s[int(n * 0.99)]
    p999 = s[min(int(n * 0.999), n - 1)]
    tput = rounds / (sum(latencies) / 1e6)  # µs total → seconds
    return BenchResult(name, rounds, p50, p99, p999, tput)


# ── Reporter ──────────────────────────────────────────────────────────────────

def _bar(value: float, ref: float, width: int = 20) -> str:
    filled = min(int((value / ref) * width), width)
    return "█" * filled + "░" * (width - filled)


def report(result: BenchResult) -> None:
    print(f"\n{'─' * 60}")
    print(f"  {result.name:40s}  n={result.rounds}")
    print(f"{'─' * 60}")
    print(f"  p50   {result.p50_us:8.1f} µs  {_bar(result.p50_us, result.p999_us)}")
    print(f"  p99   {result.p99_us:8.1f} µs  {_bar(result.p99_us,  result.p999_us)}")
    print(f"  p999  {result.p999_us:8.1f} µs  {_bar(result.p999_us, result.p999_us)}")
    print(f"  tput  {result.throughput_ops:8,.0f} ops/sec")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Matching engine benchmark")
    parser.add_argument("--rounds", type=int, default=2_000, help="Iterations per scenario")
    parser.add_argument("--warmup", type=int, default=100, help="Warmup iterations (discarded)")
    args = parser.parse_args()

    print("\n╔══════════════════════════════════════════════════════════╗")
    print("║          Mini HFT — Matching Engine Benchmark            ║")
    print("║  Python baseline. C++ (M7) target: 10-100× improvement  ║")
    print("╚══════════════════════════════════════════════════════════╝")

    # Warmup: load Python bytecache, fill JIT tiers
    print(f"\nWarming up ({args.warmup} rounds)…", end="", flush=True)
    bench_resting_orders(args.warmup)
    bench_single_fill(args.warmup)
    print(" done")

    print(f"\nRunning {args.rounds:,} rounds each…")
    results = [
        bench_resting_orders(args.rounds),
        bench_single_fill(args.rounds),
        bench_market_sweep(args.rounds, depth=5),
        bench_market_sweep(args.rounds, depth=10),
    ]
    for r in results:
        report(r)

    tput = bench_throughput(args.rounds * 2)
    print(f"\n{'─' * 60}")
    print(f"  Throughput (resting, pre-allocated):  {tput:,.0f} orders/sec")
    print(f"  Target (Python ceiling):              ~5,000 orders/sec")
    print(f"  Target C++ (M7):                      ~500,000 orders/sec")
    print(f"  Target FPGA (M8):                     ~5,000,000 orders/sec")
    print(f"{'─' * 60}\n")


if __name__ == "__main__":
    main()
