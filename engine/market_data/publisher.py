"""
Market data publisher: wraps MarketDataFeed and publishes ticks
as domain events to the EventBus.

In a real exchange feed adapter (e.g. Nasdaq ITCH, CME MDP 3.0),
this module would parse binary UDP multicast packets instead of
generating synthetic data. The interface to the rest of the system
is identical either way — the feed is just a source of TickerEvents.

Separation of concerns:
  feed.py       — price model (pure math, no I/O)
  publisher.py  — async pump that drives the feed and publishes events
  EventBus      — decouples publisher from WS broadcaster and DB writer
"""
from __future__ import annotations
import asyncio

from engine.market_data.feed import MarketDataFeed
from shared.events import EventBus, TickerEvent
from shared.constants import MARKET_DATA_TICK_INTERVAL


class MarketDataPublisher:
    """
    Background task: ticks each registered feed and publishes TickerEvents.

    Usage:
        publisher = MarketDataPublisher(event_bus, symbols=["AAPL", "TSLA"])
        task = asyncio.create_task(publisher.run())
        ...
        task.cancel()
    """

    def __init__(
        self,
        event_bus: EventBus,
        symbols: list[str],
        tick_interval: float = MARKET_DATA_TICK_INTERVAL,
    ) -> None:
        self._event_bus = event_bus
        self._tick_interval = tick_interval
        self._feeds: dict[str, MarketDataFeed] = {
            sym: MarketDataFeed(sym) for sym in symbols
        }
        self._running = False
        self._ticks_published: int = 0

    async def run(self) -> None:
        """
        Main loop. Runs until cancelled (asyncio.CancelledError).

        Each iteration ticks all feeds, publishes events, then sleeps.
        asyncio.sleep yields control so other coroutines can run during
        the sleep window — this is cooperative multitasking at work.
        """
        self._running = True
        try:
            while True:
                for symbol, feed in self._feeds.items():
                    tick = feed.next_tick()
                    self._event_bus.publish_nowait(TickerEvent(
                        symbol=symbol,
                        bid=tick.bid,
                        ask=tick.ask,
                        last=tick.last,
                        volume=tick.volume,
                        timestamp_ns=tick.timestamp_ns,
                    ))
                    self._ticks_published += 1

                await asyncio.sleep(self._tick_interval)
        except asyncio.CancelledError:
            self._running = False

    def get_current_price(self, symbol: str) -> float | None:
        feed = self._feeds.get(symbol)
        return feed.current_price if feed else None

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def ticks_published(self) -> int:
        return self._ticks_published
