"""
FastAPI application entry point.

Startup sequence (lifespan):
  1. Instantiate engine, event bus, WS manager, services
  2. Register tradeable symbols with the engine
  3. Launch background tasks (market data, WS broadcaster, heartbeat)
  4. Serve requests

Shutdown sequence:
  1. Cancel all background tasks
  2. Drain gracefully (asyncio.gather with return_exceptions=True)

Background task architecture:
  market_data_loop  — ticks synthetic feeds → publishes TickerEvents
  ws_broadcast_loop — drains EventBus → serialises → broadcasts to WS clients
  heartbeat_loop    — sends HEARTBEAT to all clients every 30s
  latency_push_loop — pushes rolling latency, PnL, and health every second
"""
from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from api.routers import market_data, metrics, orders, strategies
from api.services.book_stream_service import BookDeltaProjector
from api.services.metrics_service import MetricsService
from api.services.order_service import OrderService
from api.services.trade_tape_service import TradeTapeService
from api.websocket.manager import ConnectionManager
from api.websocket.schemas import (
    HeartbeatMessage,
    LatencySnapshotMessage,
    OrderAckMessage,
    PnlMessage,
    StrategyMetricsMessage,
    SystemHealthMessage,
    TickerMessage,
    TradeMessage,
)
from engine.core.matching_engine import MatchingEngine
from engine.market_data.publisher import MarketDataPublisher
from infra.config.settings import settings
from infra.db.database import engine as database_engine
from infra.db.writer import AsyncDatabaseWriter, PostgresCoreSink, WriterConfig
from shared.constants import DEFAULT_SYMBOLS
from shared.events import (
    BookUpdateEvent,
    DomainEvent,
    EventBus,
    EventType,
    OrderAckedEvent,
    OrderCancelledEvent,
    OrderFilledEvent,
    OrderPartiallyFilledEvent,
    TickerEvent,
    TradeEvent,
)
from strategy.runtime import StrategyRuntime

# ── Event → WS message translation ───────────────────────────────────────────

def _to_ws_message(event: DomainEvent) -> Optional[BaseModel]:
    """
    Translate a domain event to a WebSocket message.

    This is the cold path — Pydantic serialisation only happens here,
    not inside the engine. The engine knows nothing about Pydantic.
    """
    if isinstance(event, TradeEvent) and event.trade:
        t = event.trade
        return TradeMessage(
            trade_id=t.trade_id,
            symbol=t.symbol,
            price=t.price,
            quantity=t.quantity,
            notional=t.notional,
            aggressor_side=t.aggressor_side.value,
            maker_order_id=t.maker_order_id,
            taker_order_id=t.taker_order_id,
            timestamp_ns=t.timestamp_ns,
        )

    if isinstance(event, (OrderAckedEvent, OrderFilledEvent,
                           OrderPartiallyFilledEvent, OrderCancelledEvent)):
        o = event.order
        if not o:
            return None
        trades = getattr(event, "trades", [])
        return OrderAckMessage(
            order_id=o.order_id,
            client_order_id=o.client_order_id,
            symbol=o.symbol,
            side=o.side.value,
            status=o.status.value,
            quantity=o.quantity,
            filled_quantity=o.filled_quantity,
            remaining_quantity=o.remaining_quantity,
            avg_fill_price=o.avg_fill_price,
            trades_count=len(trades),
            gateway_latency_us=o.gateway_latency_us,
            processing_latency_us=o.processing_latency_us,
            roundtrip_latency_us=o.roundtrip_latency_us,
            timestamp_ns=event.timestamp_ns,
        )

    if isinstance(event, TickerEvent):
        return TickerMessage(
            symbol=event.symbol,
            bid=event.bid,
            ask=event.ask,
            last=event.last,
            volume=event.volume,
            timestamp_ns=event.timestamp_ns,
        )

    return None


# ── Background tasks ──────────────────────────────────────────────────────────

async def ws_broadcast_loop(
    event_bus: EventBus,
    ws_manager: ConnectionManager,
    book_projector: BookDeltaProjector,
) -> None:
    """
    Drain the EventBus and push messages to WebSocket clients.

    Subscribed to all event types. Depth images are projected into sequenced
    deltas; other events are translated and broadcast. Pydantic serialization
    stays in this cold-path coroutine.
    """
    q = event_bus.subscribe(
        EventType.BOOK_UPDATE,
        EventType.TRADE,
        EventType.ORDER_ACKED,
        EventType.ORDER_FILLED,
        EventType.ORDER_PARTIALLY_FILLED,
        EventType.ORDER_CANCELLED,
        EventType.ORDER_REJECTED,
        EventType.TICKER,
    )
    while True:
        event: DomainEvent = await q.get()
        msg = (
            book_projector.project(event.snapshot)
            if isinstance(event, BookUpdateEvent) and event.snapshot is not None
            else _to_ws_message(event)
        )
        if msg:
            await ws_manager.broadcast_to_symbol(event.symbol, msg)


async def heartbeat_loop(ws_manager: ConnectionManager) -> None:
    """Periodic keep-alive to all connected clients."""
    while True:
        await asyncio.sleep(settings.websocket_heartbeat_interval_s)
        await ws_manager.broadcast_all(HeartbeatMessage())


async def latency_push_loop(
    metrics_service: MetricsService,
    ws_manager: ConnectionManager,
) -> None:
    """Push rolling latency, PnL, strategy, and health projections."""
    while True:
        await asyncio.sleep(1)
        stats = metrics_service.snapshot()
        msg = LatencySnapshotMessage(
            p50_us=stats.p50_us,
            p99_us=stats.p99_us,
            p999_us=stats.p999_us,
            orders_received=stats.orders_received,
            trades_executed=stats.trades_executed,
            total_volume=stats.total_volume,
            dropped_events=stats.dropped_events,
        )
        await ws_manager.broadcast_all(msg)
        await ws_manager.broadcast_all(StrategyMetricsMessage(strategies=stats.strategy_metrics))
        await ws_manager.broadcast_all(PnlMessage(strategies=stats.strategy_metrics))
        degraded = (
            stats.dropped_events > 0
            or stats.persistence_failed_batches > 0
            or stats.strategy_callback_failures > 0
        )
        await ws_manager.broadcast_all(SystemHealthMessage(
            status="DEGRADED" if degraded else "OK",
            event_bus_drops=stats.dropped_events,
            persistence_drops=stats.persistence_dropped_events,
            persistence_failed_batches=stats.persistence_failed_batches,
            strategy_callback_failures=stats.strategy_callback_failures,
        ))


# ── Application factory ───────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle hook (replaces deprecated @on_event)."""
    engine: MatchingEngine = app.state.engine
    event_bus: EventBus = app.state.event_bus
    ws_manager: ConnectionManager = app.state.ws_manager
    persistence_writer: AsyncDatabaseWriter | None = app.state.persistence_writer
    strategy_runtime: StrategyRuntime = app.state.strategy_runtime
    metrics_service: MetricsService = app.state.metrics_service
    trade_tape_service: TradeTapeService = app.state.trade_tape_service

    # Register instruments
    for sym in DEFAULT_SYMBOLS:
        engine.register_symbol(sym)

    # Market data publisher
    publisher = MarketDataPublisher(
        event_bus=event_bus,
        symbols=DEFAULT_SYMBOLS,
        tick_interval=settings.market_data_tick_interval,
    )
    app.state.publisher = publisher

    # Spin up background tasks
    tasks = [
        asyncio.create_task(publisher.run()),
        asyncio.create_task(ws_broadcast_loop(event_bus, ws_manager, app.state.book_projector)),
        asyncio.create_task(heartbeat_loop(ws_manager)),
        asyncio.create_task(latency_push_loop(metrics_service, ws_manager)),
        asyncio.create_task(metrics_service.run()),
        asyncio.create_task(trade_tape_service.run()),
    ]
    writer_task: asyncio.Task[None] | None = None
    if persistence_writer:
        writer_task = asyncio.create_task(persistence_writer.run())
    strategy_task = asyncio.create_task(strategy_runtime.run())

    yield  # ← Application serves requests here

    # Stop producers first, then drain records already admitted to persistence.
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    strategy_task.cancel()
    await asyncio.gather(strategy_task, return_exceptions=True)
    if persistence_writer and writer_task:
        persistence_writer.request_stop()
        await writer_task
    await database_engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.api_title,
        version=settings.api_version,
        description=(
            "Low-latency trading simulator with real order matching engine. "
            "Features: CLOB matching, real-time WebSocket feed, latency profiling, "
            "strategy sandbox."
        ),
        lifespan=lifespan,
    )

    # ── State (singletons) ────────────────────────────────────────────────────
    app.state.engine = MatchingEngine()
    app.state.event_bus = EventBus(queue_depth=settings.event_queue_depth)
    app.state.ws_manager = ConnectionManager()
    app.state.order_service = OrderService(app.state.engine, app.state.event_bus)
    app.state.strategy_runtime = StrategyRuntime(app.state.event_bus, app.state.order_service)
    app.state.book_projector = BookDeltaProjector()
    app.state.trade_tape_service = TradeTapeService(app.state.event_bus)
    app.state.persistence_writer = None
    if settings.persistence_enabled:
        app.state.persistence_writer = AsyncDatabaseWriter(
            event_bus=app.state.event_bus,
            sink=PostgresCoreSink(database_engine),
            config=WriterConfig(
                batch_size=settings.persistence_batch_size,
                flush_interval_s=settings.persistence_flush_interval_ms / 1_000.0,
                max_retries=settings.persistence_max_retries,
                retry_backoff_s=settings.persistence_retry_backoff_ms / 1_000.0,
            ),
        )
    app.state.metrics_service = MetricsService(
        app.state.engine,
        app.state.event_bus,
        app.state.persistence_writer,
        app.state.strategy_runtime,
    )

    # ── Middleware ────────────────────────────────────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Routers ───────────────────────────────────────────────────────────────
    app.include_router(orders.router,      prefix="/api/v1/orders",       tags=["Orders"])
    app.include_router(market_data.router, prefix="/api/v1/market-data",  tags=["Market Data"])
    app.include_router(metrics.router,     prefix="/api/v1/metrics",      tags=["Metrics"])
    app.include_router(strategies.router,  prefix="/api/v1/strategies",   tags=["Strategies"])

    # ── Health ────────────────────────────────────────────────────────────────
    @app.get("/health", tags=["Health"])
    async def health() -> dict:
        current = app.state.metrics_service.snapshot()
        degraded = (
            current.dropped_events > 0
            or current.persistence_failed_batches > 0
            or current.strategy_callback_failures > 0
        )
        return {
            "status": "degraded" if degraded else "ok",
            "version": settings.api_version,
            "timestamp_ns": time.time_ns(),
        }

    return app


app = create_app()
