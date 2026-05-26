"""
Order management REST endpoints.

POST   /api/v1/orders/          — submit a new order
DELETE /api/v1/orders/{id}      — cancel a resting order
GET    /api/v1/orders/{id}      — query order state

REST is the slow path — used for order submission and querying.
The fast path for real-time state is WebSocket (BOOK_UPDATE / ORDER_ACK).
"""
from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from api.dependencies import get_order_service
from api.services.order_service import OrderService
from engine.core.order import Order
from engine.core.types import OrderSide, OrderType, TimeInForce

router = APIRouter()


# ── Request / Response DTOs ────────────────────────────────────────────────────

class OrderRequest(BaseModel):
    symbol: str = Field(..., examples=["AAPL"])
    side: OrderSide
    order_type: OrderType
    quantity: int = Field(..., gt=0, examples=[100])
    price: Optional[float] = Field(None, gt=0, examples=[150.25])
    time_in_force: TimeInForce = TimeInForce.GTC
    strategy_id: Optional[str] = Field(None, examples=["mm_v1"])
    client_order_id: Optional[str] = Field(None, examples=["my-order-001"])


class TradeResponse(BaseModel):
    trade_id: str
    price: float
    quantity: int
    notional: float
    aggressor_side: str
    maker_order_id: str
    taker_order_id: str
    timestamp_ns: int


class OrderResponse(BaseModel):
    order_id: str
    client_order_id: Optional[str]
    symbol: str
    side: str
    order_type: str
    quantity: int
    price: Optional[float]
    status: str
    filled_quantity: int
    remaining_quantity: int
    avg_fill_price: Optional[float]
    trades: list[TradeResponse]

    # Latency breakdown (µs) — the educational centrepiece
    gateway_latency_us: Optional[float]
    processing_latency_us: Optional[float]
    roundtrip_latency_us: Optional[float]


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post(
    "/",
    response_model=OrderResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Submit a new order",
)
async def submit_order(
    req: OrderRequest,
    order_service: OrderService = Depends(get_order_service),
) -> OrderResponse:
    """
    Submit a LIMIT or MARKET order to the matching engine.

    Returns immediately with the match result. If the order rested in
    the book, status will be OPEN. If it matched immediately, FILLED or
    PARTIALLY_FILLED. The latency fields show exactly how long each phase
    of processing took.
    """
    order = Order(
        symbol=req.symbol,
        side=req.side,
        order_type=req.order_type,
        quantity=req.quantity,
        price=req.price,
        time_in_force=req.time_in_force,
        strategy_id=req.strategy_id,
        client_order_id=req.client_order_id,
        created_at_ns=time.time_ns(),
    )

    result = order_service.submit(order)

    trades_out = [
        TradeResponse(
            trade_id=t.trade_id,
            price=t.price,
            quantity=t.quantity,
            notional=t.notional,
            aggressor_side=t.aggressor_side.value,
            maker_order_id=t.maker_order_id,
            taker_order_id=t.taker_order_id,
            timestamp_ns=t.timestamp_ns,
        )
        for t in result.trades
    ]

    return OrderResponse(
        order_id=order.order_id,
        client_order_id=order.client_order_id,
        symbol=order.symbol,
        side=order.side.value,
        order_type=order.order_type.value,
        quantity=order.quantity,
        price=order.price,
        status=order.status.value,
        filled_quantity=order.filled_quantity,
        remaining_quantity=order.remaining_quantity,
        avg_fill_price=order.avg_fill_price,
        trades=trades_out,
        gateway_latency_us=order.gateway_latency_us,
        processing_latency_us=order.processing_latency_us,
        roundtrip_latency_us=order.roundtrip_latency_us,
    )


@router.delete(
    "/{order_id}",
    summary="Cancel a resting order",
)
async def cancel_order(
    order_id: str,
    order_service: OrderService = Depends(get_order_service),
) -> dict:
    cancelled = order_service.cancel(order_id)
    if not cancelled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Order '{order_id}' not found or not cancellable",
        )
    return {
        "order_id": order_id,
        "status": cancelled.status.value,
        "message": "Order cancelled successfully",
    }


@router.get(
    "/{order_id}",
    summary="Get order by ID",
)
async def get_order(
    order_id: str,
    order_service: OrderService = Depends(get_order_service),
) -> dict:
    order = order_service.get_order(order_id)
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Order '{order_id}' not found",
        )
    return {
        "order_id": order.order_id,
        "client_order_id": order.client_order_id,
        "symbol": order.symbol,
        "side": order.side.value,
        "order_type": order.order_type.value,
        "status": order.status.value,
        "quantity": order.quantity,
        "filled_quantity": order.filled_quantity,
        "remaining_quantity": order.remaining_quantity,
        "price": order.price,
        "avg_fill_price": order.avg_fill_price,
        "processing_latency_us": order.processing_latency_us,
    }
