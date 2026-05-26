from __future__ import annotations

from fastapi.testclient import TestClient

from api.main import create_app
from infra.config.settings import settings


def test_websocket_subscription_starts_with_recovery_snapshot(monkeypatch) -> None:
    monkeypatch.setattr(settings, "persistence_enabled", False)
    with TestClient(create_app()) as client:
        response = client.get("/api/v1/market-data/AAPL/snapshot")
        assert response.status_code == 200

        with client.websocket_connect("/api/v1/market-data/ws") as websocket:
            websocket.send_json({"action": "subscribe", "symbol": "AAPL"})
            bootstrap = websocket.receive_json()
            assert bootstrap["type"] == "BOOK_SNAPSHOT"
            assert bootstrap["symbol"] == "AAPL"
