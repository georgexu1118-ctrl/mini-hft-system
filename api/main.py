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
  latency_push_loop — pushes LatencySnapshot every 5s to all clients
"""
from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from api.routers import market_data, metrics, orders
from api.services.metrics_service import MetricsService
from api.services.order_service import OrderService
from api.websocket.manager import ConnectionManager
from api.websocket.schemas import (
    BookLevel,
    BookUpdateMessage,
    HeartbeatMessage,
    LatencySnapshotMessage,
    OrderAckMessage,
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

# ── Event → WS message translation ───────────────────────────────────────────

def _to_ws_message(event: DomainEvent) -> Optional[BaseModel]:
    """
    Translate a domain event to a WebSocket message.

    This is the cold path — Pydantic serialisation only happens here,
    not inside the engine. The engine knows nothing about Pydantic.
    """
    if isinstance(event, (BookUpdateEvent,)) and event.snapshot:
        snap = event.snapshot
        return BookUpdateMessage(
            symbol=snap.symbol,
            sequence=snap.sequence,
            bids=[BookLevel(price=p, quantity=q, order_count=c) for p, q, c in snap.bids],
            asks=[BookLevel(price=p, quantity=q, order_count=c) for p, q, c in snap.asks],
            spread=snap.spread,
            mid_price=snap.mid_price,
            timestamp_ns=snap.timestamp_ns,
        )

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

async def ws_broadcast_loop(event_bus: EventBus, ws_manager: ConnectionManager) -> None:
    """
    Drain the EventBus and push messages to WebSocket clients.

    Subscribed to all event types. On each event: translate → serialise →
    broadcast. This coroutine is the only place Pydantic serialisation happens.
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
        msg = _to_ws_message(event)
        if msg:
            await ws_manager.broadcast_to_symbol(event.symbol, msg)


async def heartbeat_loop(ws_manager: ConnectionManager) -> None:
    """Periodic keep-alive to all connected clients."""
    while True:
        await asyncio.sleep(settings.websocket_heartbeat_interval_s)
        await ws_manager.broadcast_all(HeartbeatMessage())


async def latency_push_loop(
    engine: MatchingEngine,
    event_bus: EventBus,
    ws_manager: ConnectionManager,
) -> None:
    """Push LatencySnapshot every 5 seconds so the dashboard stays fresh."""
    while True:
        await asyncio.sleep(5)
        stats = engine.stats
        msg = LatencySnapshotMessage(
            p50_us=stats.p50_us,
            p99_us=stats.p99_us,
            p999_us=stats.p999_us,
            orders_received=stats.orders_received,
            trades_executed=stats.trades_executed,
            total_volume=stats.total_volume,
            dropped_events=event_bus.dropped_events,
        )
        await ws_manager.broadcast_all(msg)


# ── Application factory ───────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle hook (replaces deprecated @on_event)."""
    engine: MatchingEngine = app.state.engine
    event_bus: EventBus = app.state.event_bus
    ws_manager: ConnectionManager = app.state.ws_manager
    persistence_writer: AsyncDatabaseWriter | None = app.state.persistence_writer

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
        asyncio.create_task(ws_broadcast_loop(event_bus, ws_manager)),
        asyncio.create_task(heartbeat_loop(ws_manager)),
        asyncio.create_task(latency_push_loop(engine, event_bus, ws_manager)),
    ]
    writer_task: asyncio.Task[None] | None = None
    if persistence_writer:
        writer_task = asyncio.create_task(persistence_writer.run())

    yield  # ← Application serves requests here

    # Stop producers first, then drain records already admitted to persistence.
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
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

    # ── Health ────────────────────────────────────────────────────────────────
    @app.get("/health", tags=["Health"])
    async def health() -> dict:
        return {
            "status": "ok",
            "version": settings.api_version,
            "timestamp_ns": time.time_ns(),
        }

    return app


app = create_app()
