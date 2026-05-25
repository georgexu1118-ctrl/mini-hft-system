"""
Simple symmetric market-making strategy.

A market maker simultaneously quotes a bid and ask around the mid-price,
collecting the spread from both sides. This is the most common HFT strategy.

Key risks:
  Inventory risk       — position accumulates if one side fills repeatedly
  Adverse selection    — informed traders pick off your quotes before you move
  Volatility risk      — wide spreads protect margins but reduce fill rate

This implementation is intentionally simple (no inventory skewing, no
volatility filter) to illustrate the core concept cleanly.

Real MM strategies add:
  - Quote skewing based on current position (reduce long-side when long)
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
    Posts symmetric bid/ask quotes around the mid-price.

    Parameters
    ──────────
    spread_ticks   Dollar offset from mid-price for each quote.
                   e.g. mid=100.00, spread_ticks=0.05 →
                        bid=99.95, ask=100.05
    quote_size     Shares offered on each side per quote cycle.
    requote_threshold  Move mid-price by this many dollars before
                       cancelling old quotes and requoting.
    """

    def __init__(
        self,
        strategy_id: str,
        symbol: str,
        spread_ticks: float = 0.05,
        quote_size: int = 100,
        requote_threshold: float = 0.02,
        capital: float = 100_000.0,
        max_position: int = 500,
    ) -> None:
        super().__init__(strategy_id, symbol, capital, max_position)
        self.spread_ticks = spread_ticks
        self.quote_size = quote_size
        self.requote_threshold = requote_threshold

        self._last_quoted_mid: float | None = None
        self._active_bid_id: str | None = None
        self._active_ask_id: str | None = None

    def on_book_update(self, snapshot: BookSnapshot) -> list[Order]:
        if not self._is_running or not snapshot.mid_price:
            return []

        mid = snapshot.mid_price

        # Risk gate: don't add to a lopsided position
        if abs(self.position) >= self.max_position:
            return []

        # Requote only when mid moves meaningfully (avoid churn and fees)
        if self._last_quoted_mid is not None:
            if abs(mid - self._last_quoted_mid) < self.requote_threshold:
                return []

        self._last_quoted_mid = mid
        bid_price = round(mid - self.spread_ticks, 2)
        ask_price = round(mid + self.spread_ticks, 2)

        orders: list[Order] = []

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
