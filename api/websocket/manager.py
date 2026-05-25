"""
WebSocket connection manager.

Tracks all connected clients and their symbol subscriptions.
Provides broadcast methods for server-push messages.

Multi-server note: this in-process manager works for a single-server
deployment. To support horizontal scaling (multiple API pods behind a
load balancer), replace the in-process sets with a Redis Pub/Sub layer
so broadcasts reach clients regardless of which pod they connected to.
"""
from __future__ import annotations
import asyncio
from collections import defaultdict

from fastapi import WebSocket
from pydantic import BaseModel


class ConnectionManager:
    """
    Manages the lifecycle and subscriptions of WebSocket connections.

    Data model:
        _connections:  ws → {subscribed symbols}
        _symbol_subs:  symbol → {ws, ...}
        _global_subs:  {ws, ...}  (receive all symbols without filtering)
    """

    def __init__(self) -> None:
        self._connections: dict[WebSocket, set[str]] = {}
        self._symbol_subs: dict[str, set[WebSocket]] = defaultdict(set)
        self._global_subs: set[WebSocket] = set()

    # ── Connection lifecycle ──────────────────────────────────────────────────

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._connections[ws] = set()

    def disconnect(self, ws: WebSocket) -> None:
        """Remove all subscriptions and the connection entry."""
        syms = self._connections.pop(ws, set())
        for sym in syms:
            self._symbol_subs[sym].discard(ws)
        self._global_subs.discard(ws)

    # ── Subscription management ───────────────────────────────────────────────

    def subscribe(self, ws: WebSocket, symbol: str) -> None:
        if ws in self._connections:
            self._connections[ws].add(symbol)
            self._symbol_subs[symbol].add(ws)

    def subscribe_all(self, ws: WebSocket) -> None:
        """Subscribe to every symbol — receives all broadcasts."""
        if ws in self._connections:
            self._global_subs.add(ws)

    def unsubscribe(self, ws: WebSocket, symbol: str) -> None:
        if ws in self._connections:
            self._connections[ws].discard(symbol)
            self._symbol_subs[symbol].discard(ws)

    # ── Broadcasting ──────────────────────────────────────────────────────────

    async def broadcast_to_symbol(self, symbol: str, message: BaseModel) -> None:
        """
        Send to all clients subscribed to `symbol` plus global subscribers.

        Dead connections are collected and removed after the fan-out loop
        to avoid mutating the set while iterating.
        """
        data = message.model_dump_json()
        targets = self._symbol_subs.get(symbol, set()) | self._global_subs
        dead: list[WebSocket] = []

        for ws in list(targets):
            try:
                await ws.send_text(data)
            except Exception:
                dead.append(ws)

        for ws in dead:
            self.disconnect(ws)

    async def broadcast_all(self, message: BaseModel) -> None:
        """Send to every connected client regardless of subscriptions."""
        data = message.model_dump_json()
        dead: list[WebSocket] = []

        for ws in list(self._connections):
            try:
                await ws.send_text(data)
            except Exception:
                dead.append(ws)

        for ws in dead:
            self.disconnect(ws)

    async def send_personal(self, ws: WebSocket, message: BaseModel) -> None:
        """Send to a single connection. Used for order acks."""
        try:
            await ws.send_text(message.model_dump_json())
        except Exception:
            self.disconnect(ws)

    # ── Stats ─────────────────────────────────────────────────────────────────

    @property
    def connection_count(self) -> int:
        return len(self._connections)

    @property
    def global_subscriber_count(self) -> int:
        return len(self._global_subs)

    def subscriber_count(self, symbol: str) -> int:
        return len(self._symbol_subs.get(symbol, set()))
