"""Dashboard REST DTOs, separated from engine-owned domain structures."""
from __future__ import annotations

from pydantic import BaseModel

from engine.core.order import Trade
from engine.core.order_book import BookSnapshot


class BookLevelDTO(BaseModel):
    price: float
    quantity: int
    order_count: int


class BookSnapshotDTO(BaseModel):
    symbol: str
    sequence: int
    bids: list[BookLevelDTO]
    asks: list[BookLevelDTO]
    best_bid: float | None
    best_ask: float | None
    spread: float | None
    mid_price: float | None
    timestamp_ns: int

    @classmethod
    def from_domain(cls, snapshot: BookSnapshot) -> "BookSnapshotDTO":
        return cls(
            symbol=snapshot.symbol,
            sequence=snapshot.sequence,
            bids=[BookLevelDTO(price=p, quantity=q, order_count=c) for p, q, c in snapshot.bids],
            asks=[BookLevelDTO(price=p, quantity=q, order_count=c) for p, q, c in snapshot.asks],
            best_bid=snapshot.best_bid,
            best_ask=snapshot.best_ask,
            spread=snapshot.spread,
            mid_price=snapshot.mid_price,
            timestamp_ns=snapshot.timestamp_ns,
        )


class TradeTapeDTO(BaseModel):
    trade_id: str
    symbol: str
    price: float
    quantity: int
    notional: float
    aggressor_side: str
    timestamp_ns: int

    @classmethod
    def from_domain(cls, trade: Trade) -> "TradeTapeDTO":
        return cls(
            trade_id=trade.trade_id,
            symbol=trade.symbol,
            price=trade.price,
            quantity=trade.quantity,
            notional=trade.notional,
            aggressor_side=trade.aggressor_side.value,
            timestamp_ns=trade.timestamp_ns,
        )
