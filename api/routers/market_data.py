"""
Market data endpoints — REST snapshots and the WebSocket feed.

GET  /api/v1/market-data/{symbol}/snapshot   — point-in-time book snapshot
GET  /api/v1/market-data/symbols             — list registered symbols
WS   /api/v1/market-data/ws                  — real-time stream

WebSocket message protocol (client → server):
    {"action": "subscribe",     "symbol": "AAPL"}
    {"action": "unsubscribe",   "symbol": "AAPL"}
    {"action": "subscribe_all"}         # all symbols, all events

Server sends a BookSnapshotMessage on subscription, followed by sequenced
BookDeltaMessage, TradeMessage, TickerMessage, and OrderAckMessage frames.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, status

from api.dependencies import get_engine, get_trade_tape_service, get_ws_manager
from api.models.market_data import BookSnapshotDTO, TradeTapeDTO
from api.services.book_stream_service import BookDeltaProjector
from api.services.trade_tape_service import TradeTapeService
from api.websocket.manager import ConnectionManager
from api.websocket.schemas import ErrorMessage
from engine.hal.abstract import MatchingHAL

router = APIRouter()


# ── REST ─────────────────────────────────────────────────────────────────────

@router.get("/symbols", summary="List all registered symbols")
async def list_symbols(engine: MatchingHAL = Depends(get_engine)) -> dict:
    return {"symbols": engine.registered_symbols}


@router.get("/{symbol}/snapshot", summary="Point-in-time order book snapshot")
async def get_snapshot(
    symbol: str,
    depth: int = 10,
    engine: MatchingHAL = Depends(get_engine),
) -> BookSnapshotDTO:
    """
    Returns the current state of the order book for `symbol` up to `depth`
    levels on each side.

    Use this for initial page load. After that, subscribe via WebSocket
    and receive incremental BOOK_DELTA messages for real-time updates.
    """
    snap = engine.get_snapshot(symbol.upper(), depth)
    if snap is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Symbol '{symbol}' not found",
        )
    return BookSnapshotDTO.from_domain(snap)


@router.get("/{symbol}/trades", summary="Recent execution tape")
async def recent_trades(
    symbol: str,
    limit: int = 100,
    tape: TradeTapeService = Depends(get_trade_tape_service),
) -> list[TradeTapeDTO]:
    bounded_limit = max(1, min(limit, 500))
    return [TradeTapeDTO.from_domain(trade) for trade in tape.recent(symbol, bounded_limit)]


# ── WebSocket ─────────────────────────────────────────────────────────────────

@router.websocket("/ws")
async def websocket_endpoint(
    ws: WebSocket,
    ws_manager: ConnectionManager = Depends(get_ws_manager),
    engine: MatchingHAL = Depends(get_engine),
) -> None:
    """
    Real-time market data stream.

    On connect: client is registered but receives nothing until it subscribes.
    Subscribe messages tell the server which symbols to fan out to this client.

    The initial BOOK_SNAPSHOT is the recovery baseline. Clients apply
    subsequent BOOK_DELTA frames only while sequence is contiguous and obtain
    another snapshot if they detect a gap.

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
                    snapshot = engine.get_snapshot(symbol)
                    if snapshot is not None:
                        await ws_manager.send_personal(
                            ws,
                            BookDeltaProjector.snapshot_message(snapshot),
                        )
                else:
                    await ws_manager.send_personal(ws, ErrorMessage(
                        code="MISSING_SYMBOL",
                        message="subscribe requires a 'symbol' field",
                    ))

            elif action == "subscribe_all":
                ws_manager.subscribe_all(ws)
                for symbol in engine.registered_symbols:
                    snapshot = engine.get_snapshot(symbol)
                    if snapshot is not None:
                        await ws_manager.send_personal(
                            ws,
                            BookDeltaProjector.snapshot_message(snapshot),
                        )

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
