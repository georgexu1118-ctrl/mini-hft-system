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
from dataclasses import dataclass

from engine.core.matching_engine import MatchingEngine
from infra.db.writer import AsyncDatabaseWriter
from shared.events import EventBus


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
    registered_symbols: list[str]
    timestamp_ns: int


class MetricsService:
    def __init__(
        self,
        engine: MatchingEngine,
        event_bus: EventBus,
        persistence_writer: AsyncDatabaseWriter | None = None,
    ) -> None:
        self._engine = engine
        self._event_bus = event_bus
        self._persistence_writer = persistence_writer

    def snapshot(self) -> MetricsSnapshot:
        stats = self._engine.stats
        persistence = self._persistence_writer
        return MetricsSnapshot(
            orders_received=stats.orders_received,
            orders_matched=stats.orders_matched,
            orders_resting=stats.orders_resting,
            orders_cancelled=stats.orders_cancelled,
            orders_rejected=stats.orders_rejected,
            trades_executed=stats.trades_executed,
            total_volume=stats.total_volume,
            p50_us=stats.p50_us,
            p99_us=stats.p99_us,
            p999_us=stats.p999_us,
            dropped_events=self._event_bus.dropped_events,
            persistence_events_consumed=persistence.metrics.events_consumed if persistence else 0,
            persistence_rows_written=persistence.metrics.rows_written if persistence else 0,
            persistence_batches_written=persistence.metrics.batches_written if persistence else 0,
            persistence_write_retries=persistence.metrics.write_retries if persistence else 0,
            persistence_failed_batches=persistence.metrics.failed_batches if persistence else 0,
            persistence_dropped_events=persistence.dropped_events if persistence else 0,
            registered_symbols=self._engine.registered_symbols,
            timestamp_ns=time.time_ns(),
        )
