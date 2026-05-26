"""
Simple symmetric market-making strategy.

A market maker simultaneously quotes a bid and ask around the mid-price,
collecting the spread from both sides. This is the most common HFT strategy.

Key risks:
  Inventory risk       — position accumulates if one side fills repeatedly
  Adverse selection    — informed traders pick off your quotes before you move
  Volatility risk      — wide spreads protect margins but reduce fill rate

This implementation applies an inventory reservation-price skew and delegates
cancel/replace and hard limit enforcement to StrategyRuntime.

Real MM strategies add:
  - Cancel + requote when mid moves by > N ticks (avoid stale quotes)
  - Volatility regime detection (widen spread during high vol)
  - Fee optimisation (maker rebates vs taker fees on specific venues)
"""
from __future__ import annotations

from engine.core.order import Order, Trade
from engine.core.order_book import BookSnapshot
from engine.core.types import OrderSide, OrderType, TimeInForce
from strategy.base import Strategy


class SimpleMarketMaker(Strategy):
    """
    Posts two-sided quotes around an inventory-adjusted reservation price.

    Parameters
    ──────────
    spread_ticks   Dollar offset from mid-price for each quote.
                   e.g. mid=100.00, spread_ticks=0.05 →
                        bid=99.95, ask=100.05
    quote_size     Shares offered on each side per quote cycle.
    requote_threshold  Move mid-price by this many dollars before
                       cancelling old quotes and requoting.
    """

    replace_open_orders = True

    def __init__(
        self,
        strategy_id: str,
        symbol: str,
        spread_ticks: float = 0.05,
        quote_size: int = 100,
        requote_threshold: float = 0.02,
        inventory_skew_ticks: float = 0.10,
        capital: float = 100_000.0,
        max_position: int = 500,
    ) -> None:
        super().__init__(strategy_id, symbol, capital, max_position)
        self.spread_ticks = spread_ticks
        self.quote_size = quote_size
        self.requote_threshold = requote_threshold
        self.inventory_skew_ticks = inventory_skew_ticks

        self._last_quoted_mid: float | None = None
        self._active_bid_id: str | None = None
        self._active_ask_id: str | None = None

    def on_book_update(self, snapshot: BookSnapshot) -> list[Order]:
        if not self._is_running or snapshot.mid_price is None:
            return []

        mid = snapshot.mid_price

        # Requote only when mid moves meaningfully (avoid churn and fees)
        if self._last_quoted_mid is not None:
            if abs(mid - self._last_quoted_mid) < self.requote_threshold:
                return []

        self._last_quoted_mid = mid
        # Long inventory shifts the reservation price down: bid fills become
        # less likely while ask fills recycle risk back toward flat.
        inventory_ratio = self.position / self.max_position if self.max_position else 0.0
        reservation_price = mid - inventory_ratio * self.inventory_skew_ticks
        bid_price = round(reservation_price - self.spread_ticks, 2)
        ask_price = round(reservation_price + self.spread_ticks, 2)

        orders: list[Order] = []

        if self.position < self.max_position:
            orders.append(Order(
            symbol=self.symbol,
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=self.quote_size,
            price=bid_price,
            time_in_force=TimeInForce.GTC,
            strategy_id=self.strategy_id,
            client_order_id=f"{self.strategy_id}-bid",
            ))
        if self.position > -self.max_position:
            orders.append(Order(
            symbol=self.symbol,
            side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            quantity=self.quote_size,
            price=ask_price,
            time_in_force=TimeInForce.GTC,
            strategy_id=self.strategy_id,
            client_order_id=f"{self.strategy_id}-ask",
            ))

        return orders

    def on_trade(self, trade: Trade) -> list[Order]:
        # Passive MM: don't react to individual public trades in this version.
        # Advanced version: detect large prints that signal informed flow
        # and temporarily widen spread or pull quotes.
        return []

    def on_fill(self, order: Order, trade: Trade) -> None:
        super().on_fill(order, trade)
        # Following a fill the next book event must publish inventory-aware quotes.
        self._last_quoted_mid = None
