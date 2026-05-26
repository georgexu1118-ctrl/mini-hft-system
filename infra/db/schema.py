"""SQLAlchemy Core schema for cold-path execution persistence."""
from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Column,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Uuid,
)

# Core tables deliberately avoid ORM object materialisation. Persistence is a
# throughput-sensitive consumer, even though it is isolated from matching.
metadata = MetaData()

orders = Table(
    "orders",
    metadata,
    Column("order_id", Uuid(as_uuid=False), primary_key=True),
    Column("symbol", String(20), nullable=False),
    Column("side", String(10), nullable=False),
    Column("order_type", String(20), nullable=False),
    Column("status", String(30), nullable=False),
    Column("quantity", Integer, nullable=False),
    Column("filled_quantity", Integer, nullable=False),
    Column("price", Numeric(18, 8)),
    Column("avg_fill_price", Numeric(18, 8)),
    Column("time_in_force", String(10), nullable=False),
    Column("strategy_id", String(100)),
    Column("client_order_id", String(100)),
    Column("created_at_ns", BigInteger, nullable=False),
    Column("received_at_ns", BigInteger),
    Column("processed_at_ns", BigInteger),
    Column("acked_at_ns", BigInteger),
)

trades = Table(
    "trades",
    metadata,
    Column("trade_id", Uuid(as_uuid=False), primary_key=True),
    Column("symbol", String(20), nullable=False),
    Column("price", Numeric(18, 8), nullable=False),
    Column("quantity", Integer, nullable=False),
    Column("aggressor_side", String(10), nullable=False),
    Column("maker_order_id", Uuid(as_uuid=False), nullable=False),
    Column("taker_order_id", Uuid(as_uuid=False), nullable=False),
    Column("timestamp_ns", BigInteger, nullable=False),
)
