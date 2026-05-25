"""
FastAPI dependency functions.

All application-level singletons live on app.state, initialised in
api/main.py's lifespan. These functions extract them for DI.

Pattern: Request → app.state → singleton
This avoids global variables while keeping one instance per process.
"""
from __future__ import annotations
from fastapi import Request

from engine.core.matching_engine import MatchingEngine
from shared.events import EventBus
from api.websocket.manager import ConnectionManager
from api.services.order_service import OrderService
from api.services.metrics_service import MetricsService


def get_engine(request: Request) -> MatchingEngine:
    return request.app.state.engine


def get_event_bus(request: Request) -> EventBus:
    return request.app.state.event_bus


def get_ws_manager(request: Request) -> ConnectionManager:
    return request.app.state.ws_manager


def get_order_service(request: Request) -> OrderService:
    return request.app.state.order_service


def get_metrics_service(request: Request) -> MetricsService:
    return request.app.state.metrics_service
