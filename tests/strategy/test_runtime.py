from __future__ import annotations

import pytest

from engine.core.order import Order, Trade
from engine.core.order_book import BookSnapshot, MatchResult
from engine.core.types import OrderSide, OrderStatus, OrderType
from shared.events import BookUpdateEvent, EventBus, TradeEvent
from strategy.base import BaseStrategy
from strategy.examples.market_maker import SimpleMarketMaker
from strategy.risk import StrategyRiskGuard, StrategyRiskLimits
from strategy.runtime import StrategyRuntime


class CapturingGateway:
    def __init__(self) -> None:
        self.orders: list[Order] = []
        self.cancelled: list[str] = []

    def submit(self, order: Order) -> MatchResult:
        order.status = OrderStatus.OPEN
        self.orders.append(order)
        return MatchResult(order=order, is_new_resting=True)

    def cancel(self, order_id: str) -> Order | None:
        self.cancelled.append(order_id)
        return next((order for order in self.orders if order.order_id == order_id), None)


class OneShotStrategy(BaseStrategy):
    def __init__(self, quantity: int) -> None:
        super().__init__("alpha", "AAPL", max_position=500)
        self.quantity = quantity
        self.sent = False

    def on_book_update(self, snapshot: BookSnapshot) -> list[Order]:
        if self.sent:
            return []
        self.sent = True
        return [Order(
            "AAPL",
            OrderSide.BUY,
            OrderType.LIMIT,
            self.quantity,
            price=99.0,
            strategy_id=self.strategy_id,
        )]

    def on_trade(self, trade: Trade) -> list[Order]:
        return []


def snapshot(mid: float = 100.0) -> BookSnapshot:
    return BookSnapshot("AAPL", 1, [(mid - 0.01, 100, 1)], [(mid + 0.01, 100, 1)])


def test_runtime_applies_order_size_limit_and_kill_switch_cancels_quotes() -> None:
    bus = EventBus()
    gateway = CapturingGateway()
    runtime = StrategyRuntime(bus, gateway)
    oversized = OneShotStrategy(quantity=101)
    runtime.add_strategy(
        oversized,
        StrategyRiskGuard(StrategyRiskLimits(max_order_size=100, max_position=500)),
    )
    runtime.start_strategy("alpha")
    runtime.process_event(BookUpdateEvent(symbol="AAPL", snapshot=snapshot()))

    assert gateway.orders == []
    assert oversized.state.risk_rejections == 1

    maker = SimpleMarketMaker("mm", "AAPL", quote_size=10)
    runtime.add_strategy(maker, StrategyRiskGuard(StrategyRiskLimits(max_order_size=10)))
    runtime.start_strategy("mm")
    runtime.process_event(BookUpdateEvent(symbol="AAPL", snapshot=snapshot()))
    assert len(gateway.orders) == 2

    runtime.engage_kill_switch("mm", "loss threshold")
    assert len(gateway.cancelled) == 2
    assert maker.state.killed_reason == "loss threshold"


def test_fill_updates_position_pnl_and_inventory_skews_market_maker_quotes() -> None:
    bus = EventBus()
    gateway = CapturingGateway()
    runtime = StrategyRuntime(bus, gateway)
    maker = SimpleMarketMaker(
        "mm",
        "AAPL",
        quote_size=100,
        max_position=500,
        spread_ticks=0.05,
        inventory_skew_ticks=0.10,
    )
    runtime.add_strategy(maker)
    runtime.start_strategy("mm")
    runtime.process_event(BookUpdateEvent(symbol="AAPL", snapshot=snapshot()))
    bid = gateway.orders[0]
    trade = Trade("AAPL", 99.95, 100, OrderSide.SELL, bid.order_id, "external")
    bid.status = OrderStatus.FILLED
    runtime.process_event(TradeEvent(symbol="AAPL", trade=trade))
    runtime.process_event(BookUpdateEvent(symbol="AAPL", snapshot=snapshot()))

    assert maker.state.position == 100
    assert maker.state.average_entry_price == 99.95
    assert maker.state.unrealised_pnl == pytest.approx(5.0)
    replacement_bid, replacement_ask = gateway.orders[-2:]
    assert replacement_bid.price == 99.93
    assert replacement_ask.price == 100.03
