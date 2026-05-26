"""Bounded in-memory projection of recent public executions for dashboards."""
from __future__ import annotations

from collections import defaultdict, deque

from engine.core.order import Trade
from engine.core.types import EventType
from shared.events import EventBus, TradeEvent


class TradeTapeService:
    def __init__(self, event_bus: EventBus, max_trades_per_symbol: int = 500) -> None:
        self._queue = event_bus.subscribe(EventType.TRADE, subscriber_name="trade_tape")
        self._trades: dict[str, deque[Trade]] = defaultdict(
            lambda: deque(maxlen=max_trades_per_symbol)
        )

    async def run(self) -> None:
        while True:
            event = await self._queue.get()
            if isinstance(event, TradeEvent) and event.trade is not None:
                self.record(event.trade)

    def record(self, trade: Trade) -> None:
        self._trades[trade.symbol].appendleft(trade)

    def recent(self, symbol: str, limit: int = 100) -> list[Trade]:
        return list(self._trades[symbol.upper()])[:limit]
