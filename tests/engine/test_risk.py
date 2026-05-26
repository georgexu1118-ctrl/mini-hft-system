"""
Pre-trade risk engine tests.

Validates RiskLimits enforcement: quantity, price, and notional checks.
"""
from __future__ import annotations

import pytest

from engine.core.order import Order
from engine.core.types import OrderSide, OrderType, RejectReason
from engine.risk.limits import PreTradeRiskEngine, RiskLimits


@pytest.fixture
def limits():
    return RiskLimits(
        max_order_quantity=1_000,
        min_order_price=1.0,
        max_order_price=200.0,
        max_notional_per_order=50_000.0,
    )


@pytest.fixture
def risk(limits):
    return PreTradeRiskEngine(limits)


def _order(qty: int = 100, price: float = 100.0, side: OrderSide = OrderSide.BUY) -> Order:
    return Order(
        symbol="AAPL",
        side=side,
        order_type=OrderType.LIMIT,
        quantity=qty,
        price=price,
    )


# ── Passing checks ────────────────────────────────────────────────────────────

def test_valid_order_passes(risk):
    result = risk.check(_order(100, 100.0))
    assert result.passed


def test_valid_boundary_order_passes(risk):
    # Exactly at max notional (1000 × 50 = 50_000)
    result = risk.check(_order(1_000, 50.0))
    assert result.passed


# ── Quantity checks ───────────────────────────────────────────────────────────

def test_exceeds_max_quantity(risk):
    result = risk.check(_order(1_001, 100.0))
    assert not result.passed
    assert result.reason == RejectReason.RISK_LIMIT_EXCEEDED


def test_zero_quantity_rejected(risk):
    result = risk.check(_order(0, 100.0))
    assert not result.passed
    assert result.reason == RejectReason.INVALID_QUANTITY


# ── Price checks ──────────────────────────────────────────────────────────────

def test_exceeds_max_price(risk):
    result = risk.check(_order(100, 200.01))
    assert not result.passed
    assert result.reason == RejectReason.INVALID_PRICE


def test_below_min_price(risk):
    result = risk.check(_order(100, 0.99))
    assert not result.passed
    assert result.reason == RejectReason.INVALID_PRICE


def test_market_order_skips_price_check(risk):
    """MARKET orders have no price — should not fail price validation."""
    mkt = Order(
        symbol="AAPL",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=100,
    )
    result = risk.check(mkt)
    assert result.passed


# ── Notional checks ───────────────────────────────────────────────────────────

def test_exceeds_max_notional(risk):
    # 1000 × 51 = 51_000 > 50_000
    result = risk.check(_order(1_000, 51.0))
    assert not result.passed
    assert result.reason == RejectReason.RISK_LIMIT_EXCEEDED


def test_exactly_at_notional_limit_passes(risk):
    result = risk.check(_order(500, 100.0))  # 50_000 exactly
    assert result.passed


# ── Custom limits ─────────────────────────────────────────────────────────────

def test_strict_limits():
    engine = PreTradeRiskEngine(RiskLimits(
        max_order_quantity=10,
        min_order_price=90.0,
        max_order_price=110.0,
        max_notional_per_order=1_000.0,
    ))
    assert engine.check(_order(10, 100.0)).passed
    assert not engine.check(_order(11, 100.0)).passed   # above max qty
    assert not engine.check(_order(10, 110.01)).passed  # price too high
    assert not engine.check(_order(10, 89.99)).passed   # price too low
    assert not engine.check(_order(10, 101.0)).passed   # notional 1010 > 1000


# ── Symbol-specific limits ────────────────────────────────────────────────────

def test_symbol_override():
    engine = PreTradeRiskEngine(RiskLimits(max_order_quantity=1_000))
    engine.set_symbol_limits("TSLA", RiskLimits(max_order_quantity=10))

    aapl_order = _order(500, 100.0)
    assert engine.check(aapl_order).passed  # uses global limits

    tsla_order = Order(symbol="TSLA", side=OrderSide.BUY, order_type=OrderType.LIMIT, quantity=11, price=100.0)
    assert not engine.check(tsla_order).passed  # symbol limit: max 10
