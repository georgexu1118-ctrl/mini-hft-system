"""
FastAPI dependency functions.

All application-level singletons live on app.state, initialised in
api/main.py's lifespan. These functions extract them for DI.

Pattern: HTTPConnection (Request or WebSocket) → app.state → singleton

Swap SoftwareMatchingHAL for CppMatchingHAL / FPGAMatchingHAL (M7/M8) in
api/main.py without touching any router or dependency code here.
This avoids global variables while keeping one instance per process.
"""
from __future__ import annotations

from starlette.requests import HTTPConnection

from api.services.metrics_service import MetricsService
from api.services.order_service import OrderService
from api.services.trade_tape_service import TradeTapeService
from api.websocket.manager import ConnectionManager
from engine.hal.abstract import MatchingHAL
from shared.events import EventBus
from strategy.runtime import StrategyRuntime


def get_engine(connection: HTTPConnection) -> MatchingHAL:
    """Returns the active matching HAL (software, C++, or FPGA implementation)."""
    return connection.app.state.engine


def get_event_bus(connection: HTTPConnection) -> EventBus:
    return connection.app.state.event_bus


def get_ws_manager(connection: HTTPConnection) -> ConnectionManager:
    return connection.app.state.ws_manager


def get_order_service(connection: HTTPConnection) -> OrderService:
    return connection.app.state.order_service


def get_metrics_service(connection: HTTPConnection) -> MetricsService:
    return connection.app.state.metrics_service


def get_strategy_runtime(connection: HTTPConnection) -> StrategyRuntime:
    return connection.app.state.strategy_runtime


def get_trade_tape_service(connection: HTTPConnection) -> TradeTapeService:
    return connection.app.state.trade_tape_service
