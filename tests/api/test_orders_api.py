"""
Integration tests for the Orders REST API.

Uses FastAPI TestClient (synchronous ASGI wrapper) to test the full
HTTP layer: serialization, DI, service layer, engine, response DTOs.

These tests do NOT hit a real database (persistence disabled via monkeypatch).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from infra.config.settings import settings


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(settings, "persistence_enabled", False)
    with TestClient(create_app()) as c:
        yield c


# ── POST /api/v1/orders/ ──────────────────────────────────────────────────────

def test_submit_resting_limit_order(client):
    resp = client.post("/api/v1/orders/", json={
        "symbol": "AAPL",
        "side": "BUY",
        "order_type": "LIMIT",
        "quantity": 100,
        "price": 150.0,
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "OPEN"
    assert data["filled_quantity"] == 0
    assert data["remaining_quantity"] == 100
    assert data["order_id"] is not None


def test_submit_matching_orders_produces_fill(client):
    # Resting buy
    r1 = client.post("/api/v1/orders/", json={
        "symbol": "AAPL",
        "side": "BUY",
        "order_type": "LIMIT",
        "quantity": 100,
        "price": 150.0,
    })
    assert r1.status_code == 201

    # Aggressive sell → immediate fill
    r2 = client.post("/api/v1/orders/", json={
        "symbol": "AAPL",
        "side": "SELL",
        "order_type": "LIMIT",
        "quantity": 100,
        "price": 150.0,
    })
    assert r2.status_code == 201
    data = r2.json()
    assert data["status"] == "FILLED"
    assert data["filled_quantity"] == 100
    assert len(data["trades"]) == 1
    assert data["trades"][0]["price"] == 150.0


def test_submit_market_order(client):
    # Place resting sell first
    client.post("/api/v1/orders/", json={
        "symbol": "AAPL",
        "side": "SELL",
        "order_type": "LIMIT",
        "quantity": 50,
        "price": 151.0,
    })

    resp = client.post("/api/v1/orders/", json={
        "symbol": "AAPL",
        "side": "BUY",
        "order_type": "MARKET",
        "quantity": 50,
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["filled_quantity"] == 50
    assert data["avg_fill_price"] == 151.0


def test_ioc_order_cancelled_when_no_liquidity(client):
    resp = client.post("/api/v1/orders/", json={
        "symbol": "AAPL",
        "side": "BUY",
        "order_type": "LIMIT",
        "quantity": 100,
        "price": 150.0,
        "time_in_force": "IOC",
    })
    assert resp.status_code == 201
    assert resp.json()["status"] == "CANCELLED"


def test_latency_fields_populated(client):
    resp = client.post("/api/v1/orders/", json={
        "symbol": "AAPL",
        "side": "BUY",
        "order_type": "LIMIT",
        "quantity": 1,
        "price": 100.0,
    })
    data = resp.json()
    # processing_latency_us may be 0 on Windows time resolution — just check not None
    assert data["gateway_latency_us"] is not None
    assert data["processing_latency_us"] is not None


def test_submit_invalid_quantity_returns_422(client):
    resp = client.post("/api/v1/orders/", json={
        "symbol": "AAPL",
        "side": "BUY",
        "order_type": "LIMIT",
        "quantity": -1,  # invalid
        "price": 150.0,
    })
    assert resp.status_code == 422


def test_submit_limit_order_missing_price_returns_422(client):
    # LIMIT without a price — validated at service layer
    resp = client.post("/api/v1/orders/", json={
        "symbol": "AAPL",
        "side": "BUY",
        "order_type": "LIMIT",
        "quantity": 100,
        # price omitted
    })
    # Pydantic or service validation should catch this
    assert resp.status_code in (400, 422)


# ── DELETE /api/v1/orders/{id} ────────────────────────────────────────────────

def test_cancel_resting_order(client):
    r = client.post("/api/v1/orders/", json={
        "symbol": "AAPL",
        "side": "BUY",
        "order_type": "LIMIT",
        "quantity": 100,
        "price": 150.0,
    })
    order_id = r.json()["order_id"]

    cancel_resp = client.delete(f"/api/v1/orders/{order_id}")
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["status"] == "CANCELLED"


def test_cancel_nonexistent_order_returns_404(client):
    resp = client.delete("/api/v1/orders/does-not-exist")
    assert resp.status_code == 404


# ── GET /api/v1/orders/{id} ───────────────────────────────────────────────────

def test_get_order_by_id(client):
    r = client.post("/api/v1/orders/", json={
        "symbol": "AAPL",
        "side": "BUY",
        "order_type": "LIMIT",
        "quantity": 100,
        "price": 150.0,
    })
    order_id = r.json()["order_id"]

    get_resp = client.get(f"/api/v1/orders/{order_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["order_id"] == order_id


def test_get_nonexistent_order_returns_404(client):
    resp = client.get("/api/v1/orders/ghost-id")
    assert resp.status_code == 404


# ── GET /health ───────────────────────────────────────────────────────────────

def test_health_endpoint(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] in ("ok", "degraded")
    assert "matching_backend" in data
    assert "symbols" in data
    assert "AAPL" in data["symbols"]
