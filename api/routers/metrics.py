"""
Observability endpoints.

GET /api/v1/metrics/          — full engine stats snapshot
GET /api/v1/metrics/latency   — latency percentiles only
GET /api/v1/metrics/ws        — WebSocket connection stats

In production: expose a /metrics endpoint in Prometheus format
and scrape with a sidecar. Build Grafana dashboards on top.
"""
from __future__ import annotations
import time

from fastapi import APIRouter, Depends

from api.dependencies import get_metrics_service, get_ws_manager
from api.services.metrics_service import MetricsService
from api.websocket.manager import ConnectionManager

router = APIRouter()


@router.get("/", summary="Full engine metrics snapshot")
async def get_metrics(
    metrics_service: MetricsService = Depends(get_metrics_service),
) -> dict:
    """
    Returns all engine counters and latency percentiles.

    Poll this endpoint every few seconds to power the dashboard's
    metrics panels. The latency values are the processing_latency
    (engine queue → match complete) — the number HFT engineers care about.
    """
    snap = metrics_service.snapshot()
    return {
        "engine": {
            "orders_received": snap.orders_received,
            "orders_matched": snap.orders_matched,
            "orders_resting": snap.orders_resting,
            "orders_cancelled": snap.orders_cancelled,
            "orders_rejected": snap.orders_rejected,
            "trades_executed": snap.trades_executed,
            "total_volume": snap.total_volume,
        },
        "latency_us": {
            "p50": snap.p50_us,
            "p99": snap.p99_us,
            "p999": snap.p999_us,
            "note": "processing_latency only (engine queue → match complete)",
        },
        "infrastructure": {
            "dropped_events": snap.dropped_events,
            "registered_symbols": snap.registered_symbols,
        },
        "timestamp_ns": snap.timestamp_ns,
    }


@router.get("/latency", summary="Latency percentiles only")
async def get_latency(
    metrics_service: MetricsService = Depends(get_metrics_service),
) -> dict:
    snap = metrics_service.snapshot()
    return {
        "p50_us": snap.p50_us,
        "p99_us": snap.p99_us,
        "p999_us": snap.p999_us,
        "sample_count": snap.orders_received,
        "timestamp_ns": snap.timestamp_ns,
    }


@router.get("/ws", summary="WebSocket connection stats")
async def get_ws_stats(
    ws_manager: ConnectionManager = Depends(get_ws_manager),
) -> dict:
    return {
        "connections": ws_manager.connection_count,
        "global_subscribers": ws_manager.global_subscriber_count,
        "timestamp_ns": time.time_ns(),
    }
