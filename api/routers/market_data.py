"""
Market data endpoints — REST snapshots and the WebSocket feed.

GET  /api/v1/market-data/{symbol}/snapshot   — point-in-time book snapshot
GET  /api/v1/market-data/symbols             — list registered symbols
WS   /api/v1/market-data/ws                  — real-time stream

WebSocket message protocol (client → server):
    {"action": "subscribe",     "symbol": "AAPL"}
    {"action": "unsubscribe",   "symbol": "AAPL"}
    {"action": "subscribe_all"}         # all symbols, all events

Server will immediately start pushing BookUpdateMessage, TradeMessage,
TickerMessage, and OrderAckMessage to the client.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, status

from api.dependencies import get_engine, get_ws_manager
from api.websocket.manager import ConnectionManager
from api.websocket.schemas import ErrorMessage
from engine.core.matching_engine import MatchingEngine

router = APIRouter()


# ── REST ─────────────────────────────────────────────────────────────────────

@router.get("/symbols", summary="List all registered symbols")
async def list_symbols(engine: MatchingEngine = Depends(get_engine)) -> dict:
    return {"symbols": engine.registered_symbols}


@router.get("/{symbol}/snapshot", summary="Point-in-time order book snapshot")
async def get_snapshot(
    symbol: str,
    depth: int = 10,
    engine: MatchingEngine = Depends(get_engine),
) -> dict:
    """
    Returns the current state of the order book for `symbol` up to `depth`
    levels on each side.

    Use this for initial page load. After that, subscribe via WebSocket
    and receive incremental BOOK_UPDATE messages for real-time updates.
    """
    snap = engine.get_snapshot(symbol.upper(), depth)
    if snap is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Symbol '{symbol}' not found",
        )
    return {
        "symbol": snap.symbol,
        "sequence": snap.sequence,
        "bids": [
            {"price": p, "quantity": q, "order_count": c}
            for p, q, c in snap.bids
        ],
        "asks": [
            {"price": p, "quantity": q, "order_count": c}
            for p, q, c in snap.asks
        ],
        "best_bid": snap.best_bid,
        "best_ask": snap.best_ask,
        "spread": snap.spread,
        "mid_price": snap.mid_price,
        "timestamp_ns": snap.timestamp_ns,
    }


# ── WebSocket ─────────────────────────────────────────────────────────────────

@router.websocket("/ws")
async def websocket_endpoint(
    ws: WebSocket,
    ws_manager: ConnectionManager = Depends(get_ws_manager),
) -> None:
    """
    Real-time market data stream.

    On connect: client is registered but receives nothing until it subscribes.
    Subscribe messages tell the server which symbols to fan out to this client.

    All subsequent market events (book updates, trades, tickers, order acks)
    for subscribed symbols are pushed as JSON text frames.

    Client disconnect (graceful or abrupt) automatically cleans up all
    subscriptions — no explicit unsubscribe required on disconnect.
    """
    await ws_manager.connect(ws)
    try:
        while True:
            data = await ws.receive_json()
            action = data.get("action", "")

            if action == "subscribe":
                symbol = (data.get("symbol") or "").upper()
                if symbol:
                    ws_manager.subscribe(ws, symbol)
                else:
                    await ws_manager.send_personal(ws, ErrorMessage(
                        code="MISSING_SYMBOL",
                        message="subscribe requires a 'symbol' field",
                    ))

            elif action == "subscribe_all":
                ws_manager.subscribe_all(ws)

            elif action == "unsubscribe":
                symbol = (data.get("symbol") or "").upper()
                if symbol:
                    ws_manager.unsubscribe(ws, symbol)

            else:
                await ws_manager.send_personal(ws, ErrorMessage(
                    code="UNKNOWN_ACTION",
                    message=f"Unknown action: '{action}'",
                ))

    except WebSocketDisconnect:
        ws_manager.disconnect(ws)
    except Exception:
        ws_manager.disconnect(ws)
