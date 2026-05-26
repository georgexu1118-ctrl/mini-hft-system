"""Per-strategy pre-trade limits enforced by the strategy runtime."""
from __future__ import annotations

from dataclasses import dataclass

from engine.core.order import Order
from strategy.base import BaseStrategy


@dataclass(frozen=True, slots=True)
class StrategyRiskLimits:
    max_order_size: int = 100
    max_position: int = 500
    max_notional_per_order: float = 1_000_000.0


@dataclass(frozen=True, slots=True)
class StrategyRiskDecision:
    accepted: bool
    reason: str = ""


class StrategyRiskGuard:
    """Fast local controls before an order reaches the exchange gateway."""

    def __init__(self, limits: StrategyRiskLimits | None = None) -> None:
        self.limits = limits or StrategyRiskLimits()

    def evaluate(self, strategy: BaseStrategy, order: Order) -> StrategyRiskDecision:
        if not strategy.is_running:
            return StrategyRiskDecision(False, "strategy is stopped or killed")
        if order.quantity > self.limits.max_order_size:
            return StrategyRiskDecision(False, "max order size exceeded")
        projected = strategy.position + (order.quantity if order.is_buy else -order.quantity)
        maximum = min(strategy.max_position, self.limits.max_position)
        if abs(projected) > maximum:
            return StrategyRiskDecision(False, "inventory limit exceeded")
        if order.price is not None and order.price * order.quantity > self.limits.max_notional_per_order:
            return StrategyRiskDecision(False, "max order notional exceeded")
        return StrategyRiskDecision(True)
