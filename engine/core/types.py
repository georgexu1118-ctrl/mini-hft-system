from __future__ import annotations
from enum import Enum


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    LIMIT = "LIMIT"
    MARKET = "MARKET"


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    OPEN = "OPEN"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


class TimeInForce(str, Enum):
    # Good Till Cancel — rests in book until filled or explicitly cancelled
    GTC = "GTC"
    # Immediate or Cancel — fill what's available, cancel remainder
    IOC = "IOC"
    # Fill or Kill — fill entire order atomically or reject entirely
    FOK = "FOK"
    # Good Till Date — rests until a specified expiry timestamp
    GTD = "GTD"


class EventType(str, Enum):
    ORDER_RECEIVED = "ORDER_RECEIVED"
    ORDER_ACKED = "ORDER_ACKED"
    ORDER_FILLED = "ORDER_FILLED"
    ORDER_PARTIALLY_FILLED = "ORDER_PARTIALLY_FILLED"
    ORDER_CANCELLED = "ORDER_CANCELLED"
    ORDER_REJECTED = "ORDER_REJECTED"
    TRADE = "TRADE"
    BOOK_UPDATE = "BOOK_UPDATE"
    TICKER = "TICKER"


class RejectReason(str, Enum):
    INVALID_PRICE = "INVALID_PRICE"
    INVALID_QUANTITY = "INVALID_QUANTITY"
    SYMBOL_NOT_FOUND = "SYMBOL_NOT_FOUND"
    RISK_LIMIT_EXCEEDED = "RISK_LIMIT_EXCEEDED"
    DUPLICATE_ORDER_ID = "DUPLICATE_ORDER_ID"
    ORDER_NOT_FOUND = "ORDER_NOT_FOUND"
    INSUFFICIENT_LIQUIDITY = "INSUFFICIENT_LIQUIDITY"


# Type aliases — make intent explicit in hot-path signatures
OrderId = str
Symbol = str
Price = float
Quantity = int
TimestampNs = int
