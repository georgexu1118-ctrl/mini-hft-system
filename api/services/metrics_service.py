"""
MetricsService: aggregates engine stats and event bus telemetry.

In production: plug in Prometheus client (prometheus_client) and export
a /metrics endpoint in the Prometheus exposition format. Grafana then
builds dashboards on top of this.

For the simulator: we expose a JSON endpoint that the Next.js dashboard
can poll (or the WS broadcaster can push on a timer).
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass

from engine.hal.abstract import MatchingHAL
from engine.core.types import EventType
from infra.db.writer import AsyncDatabaseWriter
from shared.events import (
    EventBus,
    OrderAckedEvent,
    OrderFilledEvent,
    OrderPartiallyFilledEvent,
)
from strategy.runtime import StrategyRuntime


class RollingLatencyWindow:
    """Time-bounded latency samples used for current dashboard tail latency."""

    def __init__(self, horizon_s: int = 60, max_samples: int = 20_000) -> None:
        self._horizon_ns = horizon_s * 1_000_000_000
        self._samples: deque[tuple[int, float]] = deque(maxlen=max_samples)

    def record(self, timestamp_ns: int, latency_us: float) -> None:
        self._samples.append((timestamp_ns, latency_us))
        self._evict(timestamp_ns)

    def percentile(self, percentile: float) -> float | None:
        self._evict(time.time_ns())
        if not self._samples:
            return None
        ordered = sorted(latency for _, latency in self._samples)
        index = min(int(len(ordered) * percentile / 100), len(ordered) - 1)
        return ordered[index]

    @property
    def sample_count(self) -> int:
        self._evict(time.time_ns())
        return len(self._samples)

    def _evict(self, timestamp_ns: int) -> None:
        cutoff = timestamp_ns - self._horizon_ns
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()


@dataclass
class MetricsSnapshot:
    # Engine counters
    orders_received: int
    orders_matched: int
    orders_resting: int
    orders_cancelled: int
    orders_rejected: int
    trades_executed: int
    total_volume: int

    # Latency percentiles (microseconds, None if no data)
    p50_us: float | None
    p99_us: float | None
    p999_us: float | None

    # Infrastructure
    dropped_events: int
    persistence_events_consumed: int
    persistence_rows_written: int
    persistence_batches_written: int
    persistence_write_retries: int
    persistence_failed_batches: int
    persistence_dropped_events: int
    latency_sample_count: int
    strategy_metrics: dict[str, dict[str, float | int | str | None]]
    strategy_callback_failures: int
    strategy_dropped_events: int
    registered_symbols: list[str]
    timestamp_ns: int


class MetricsService:
    def __init__(
        self,
        engine: MatchingHAL,
        event_bus: EventBus,
        persistence_writer: AsyncDatabaseWriter | None = None,
        strategy_runtime: StrategyRuntime | None = None,
    ) -> None:
        self._engine = engine
        self._event_bus = event_bus
        self._persistence_writer = persistence_writer
        self._strategy_runtime = strategy_runtime
        self._latencies = RollingLatencyWindow()
        self._queue = event_bus.subscribe(
            EventType.ORDER_ACKED,
            EventType.ORDER_FILLED,
            EventType.ORDER_PARTIALLY_FILLED,
            subscriber_name="metrics",
        )

    async def run(self) -> None:
        """Observe completed orders on a private queue; no instrumentation in matching."""
        while True:
            event = await self._queue.get()
            if isinstance(event, (OrderAckedEvent, OrderFilledEvent, OrderPartiallyFilledEvent)):
                order = event.order
                if order is not None and order.processing_latency_us is not None:
                    self._latencies.record(event.timestamp_ns, order.processing_latency_us)

    def snapshot(self) -> MetricsSnapshot:
        stats = self._engine.stats
        persistence = self._persistence_writer
        strategy = self._strategy_runtime
        p50 = self._latencies.percentile(50)
        p99 = self._latencies.percentile(99)
        p999 = self._latencies.percentile(99.9)
        return MetricsSnapshot(
            orders_received=stats.orders_received,
            orders_matched=stats.orders_matched,
            orders_resting=stats.orders_resting,
            orders_cancelled=stats.orders_cancelled,
            orders_rejected=stats.orders_rejected,
            trades_executed=stats.trades_executed,
            total_volume=stats.total_volume,
            p50_us=p50 if p50 is not None else stats.p50_us,
            p99_us=p99 if p99 is not None else stats.p99_us,
            p999_us=p999 if p999 is not None else stats.p999_us,
            dropped_events=self._event_bus.dropped_events,
            persistence_events_consumed=persistence.metrics.events_consumed if persistence else 0,
            persistence_rows_written=persistence.metrics.rows_written if persistence else 0,
            persistence_batches_written=persistence.metrics.batches_written if persistence else 0,
            persistence_write_retries=persistence.metrics.write_retries if persistence else 0,
            persistence_failed_batches=persistence.metrics.failed_batches if persistence else 0,
            persistence_dropped_events=persistence.dropped_events if persistence else 0,
            latency_sample_count=self._latencies.sample_count,
            strategy_metrics=strategy.strategy_metrics() if strategy else {},
            strategy_callback_failures=strategy.metrics.callback_failures if strategy else 0,
            strategy_dropped_events=(
                self._event_bus.dropped_events_for(StrategyRuntime.SUBSCRIBER_NAME)
                if strategy
                else 0
            ),
            registered_symbols=self._engine.registered_symbols,
            timestamp_ns=time.time_ns(),
        )
