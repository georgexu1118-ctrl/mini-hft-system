"""
Pre-trade risk checks ("pre-trade risk" or "PTR").

In production HFT infrastructure, risk checks happen at multiple layers:
  1. Hardware/FPGA layer  — reject obviously invalid orders in nanoseconds
  2. Software gateway     — check limits and rate limits before the engine
  3. Inside the engine    — final validation (symbol exists, price > 0, etc.)

This module implements layer 2: a configurable set of soft limits that
the service layer applies *before* calling MatchingEngine.submit_order.

Design note: risk checks should be *fast and stateless* where possible.
Stateful checks (e.g. open P&L, net position across strategies) require
a shared store and introduce latency. Only add stateful checks where the
business risk justifies the cost.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

from engine.core.order import Order
from engine.core.types import RejectReason, OrderType
from shared.constants import MAX_ORDER_QUANTITY, MIN_ORDER_PRICE, MAX_ORDER_PRICE


@dataclass
class RiskLimits:
    """
    Per-symbol (or global) risk limit configuration.

    In production: loaded from a configuration database, hot-reloadable
    without restarting the engine.
    """
    max_order_quantity: int = MAX_ORDER_QUANTITY
    min_order_price: float = MIN_ORDER_PRICE
    max_order_price: float = MAX_ORDER_PRICE
    max_orders_per_second: int = 1_000   # rate limit (not yet enforced here)
    max_notional_per_order: float = 10_000_000.0   # $10M per order


@dataclass
class RiskCheckResult:
    passed: bool
    reason: Optional[RejectReason] = None
    detail: str = ""


class PreTradeRiskEngine:
    """
    Stateless pre-trade risk checker.

    Returns RiskCheckResult — never raises. The caller decides what to do
    with a failed check (reject the order, log it, alert the risk desk).
    """

    def __init__(self, limits: RiskLimits | None = None) -> None:
        self._limits = limits or RiskLimits()
        self._symbol_limits: dict[str, RiskLimits] = {}

    def set_symbol_limits(self, symbol: str, limits: RiskLimits) -> None:
        """Override global limits for a specific symbol."""
        self._symbol_limits[symbol] = limits

    def check(self, order: Order) -> RiskCheckResult:
        """Run all pre-trade checks on an order. O(1)."""
        limits = self._symbol_limits.get(order.symbol, self._limits)

        # Quantity checks
        if order.quantity <= 0:
            return RiskCheckResult(
                passed=False,
                reason=RejectReason.INVALID_QUANTITY,
                detail=f"quantity={order.quantity} must be > 0",
            )
        if order.quantity > limits.max_order_quantity:
            return RiskCheckResult(
                passed=False,
                reason=RejectReason.RISK_LIMIT_EXCEEDED,
                detail=f"quantity={order.quantity} exceeds max={limits.max_order_quantity}",
            )

        # Price checks (limit orders only)
        if order.order_type == OrderType.LIMIT:
            if order.price is None or order.price < limits.min_order_price:
                return RiskCheckResult(
                    passed=False,
                    reason=RejectReason.INVALID_PRICE,
                    detail=f"price={order.price} below min={limits.min_order_price}",
                )
            if order.price > limits.max_order_price:
                return RiskCheckResult(
                    passed=False,
                    reason=RejectReason.INVALID_PRICE,
                    detail=f"price={order.price} exceeds max={limits.max_order_price}",
                )
            # Notional check
            notional = order.price * order.quantity
            if notional > limits.max_notional_per_order:
                return RiskCheckResult(
                    passed=False,
                    reason=RejectReason.RISK_LIMIT_EXCEEDED,
                    detail=f"notional=${notional:,.2f} exceeds max=${limits.max_notional_per_order:,.2f}",
                )

        return RiskCheckResult(passed=True)
