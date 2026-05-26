"""Translate domain events into database records outside the engine boundary."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from shared.events import (
    DomainEvent,
    OrderAckedEvent,
    OrderCancelledEvent,
    OrderFilledEvent,
    OrderPartiallyFilledEvent,
    OrderRejectedEvent,
    TradeEvent,
)


@dataclass(frozen=True, slots=True)
class PersistenceRecords:
    """Rows produced by one event; empty collections require no DB work."""

    orders: tuple[dict[str, Any], ...] = ()
    trades: tuple[dict[str, Any], ...] = ()


def serialize_event(event: DomainEvent) -> PersistenceRecords:
    """Build plain Core parameter mappings; Pydantic/ORM stay off this path."""
    if isinstance(event, TradeEvent) and event.trade is not None:
        trade = event.trade
        return PersistenceRecords(
            trades=({
                "trade_id": trade.trade_id,
                "symbol": trade.symbol,
                "price": trade.price,
                "quantity": trade.quantity,
                "aggressor_side": trade.aggressor_side.value,
                "maker_order_id": trade.maker_order_id,
                "taker_order_id": trade.taker_order_id,
                "timestamp_ns": trade.timestamp_ns,
            },),
        )

    order_events = (
        OrderAckedEvent,
        OrderFilledEvent,
        OrderPartiallyFilledEvent,
        OrderCancelledEvent,
        OrderRejectedEvent,
    )
    if isinstance(event, order_events) and event.order is not None:
        order = event.order
        return PersistenceRecords(
            orders=({
                "order_id": order.order_id,
                "symbol": order.symbol,
                "side": order.side.value,
                "order_type": order.order_type.value,
                "status": order.status.value,
                "quantity": order.quantity,
                "filled_quantity": order.filled_quantity,
                "price": order.price,
                "avg_fill_price": order.avg_fill_price,
                "time_in_force": order.time_in_force.value,
                "strategy_id": order.strategy_id,
                "client_order_id": order.client_order_id,
                "created_at_ns": order.created_at_ns,
                "received_at_ns": order.received_at_ns or None,
                "processed_at_ns": order.processed_at_ns or None,
                "acked_at_ns": order.acked_at_ns or None,
            },),
        )
    return PersistenceRecords()
