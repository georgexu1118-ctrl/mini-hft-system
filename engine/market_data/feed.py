"""
Synthetic market data generator using Geometric Brownian Motion.

GBM is the standard model for equity price simulation (Black-Scholes assumes it).
dS = S * (µ dt + σ dW)
where W is a Wiener process (Brownian motion), µ is drift, σ is volatility.

Discrete approximation (Euler-Maruyama):
  S(t+dt) = S(t) * exp((µ - σ²/2)dt + σ√dt * Z)
  where Z ~ N(0,1)

This isn't a faithful market microstructure model — real order books have
autocorrelated volatility (GARCH), intraday U-shapes, and correlated ticks.
But it's sufficient to generate a realistic-looking price series for
demonstrating latency measurement and strategy sandboxing.

Future: plug in a replay feed from real historical data (e.g. Nasdaq ITCH).
"""
from __future__ import annotations
import math
import random
import time
from dataclasses import dataclass, field

from shared.constants import (
    DEFAULT_INITIAL_PRICE,
    DEFAULT_TICK_VOLATILITY,
    DEFAULT_BID_ASK_SPREAD,
    DEFAULT_BASE_VOLUME,
)


@dataclass(slots=True)
class Tick:
    """Single market data tick for one symbol."""
    symbol: str
    bid: float
    ask: float
    last: float       # last trade price (approximated as mid)
    volume: int       # synthetic tick volume (log-normal)
    timestamp_ns: int


@dataclass
class FeedConfig:
    """Tunable parameters for one symbol's feed."""
    initial_price: float = DEFAULT_INITIAL_PRICE
    volatility: float = DEFAULT_TICK_VOLATILITY
    drift: float = 0.0                          # zero drift = random walk
    spread: float = DEFAULT_BID_ASK_SPREAD
    base_volume: int = DEFAULT_BASE_VOLUME
    volume_sigma: float = 0.5                   # log-normal shape param


class MarketDataFeed:
    """
    Stateful price generator for a single symbol.

    Call next_tick() at regular intervals (e.g. every 100 ms) to get
    a new synthetic market data point. The internal price state is
    mutated on every call — this is intentional; the feed has memory.

    Thread-safety: not thread-safe. Designed to run in a single
    asyncio task that owns the feed exclusively.
    """

    def __init__(self, symbol: str, config: FeedConfig | None = None) -> None:
        self.symbol = symbol
        self._cfg = config or FeedConfig()
        self._price = self._cfg.initial_price
        self._tick_count: int = 0

    def next_tick(self) -> Tick:
        """
        Advance price by one step using GBM and return a Tick.

        The price walk: S(t+1) = S(t) * exp((µ - σ²/2) + σ * Z)
        Bid  = mid - spread/2
        Ask  = mid + spread/2
        Volume: log-normal with median ≈ base_volume
        """
        cfg = self._cfg
        z = random.gauss(0, 1)
        self._price *= math.exp(
            (cfg.drift - 0.5 * cfg.volatility ** 2) + cfg.volatility * z
        )
        # Clamp to avoid degenerate prices
        self._price = max(self._price, 0.01)

        half_spread = cfg.spread / 2
        bid = round(self._price - half_spread, 2)
        ask = round(self._price + half_spread, 2)
        last = round(self._price, 2)

        # Log-normal volume: most ticks small, occasional large prints
        volume = max(1, int(random.lognormvariate(
            math.log(cfg.base_volume), cfg.volume_sigma
        )))

        self._tick_count += 1
        return Tick(
            symbol=self.symbol,
            bid=bid,
            ask=ask,
            last=last,
            volume=volume,
            timestamp_ns=time.time_ns(),
        )

    @property
    def current_price(self) -> float:
        return self._price

    @property
    def tick_count(self) -> int:
        return self._tick_count

    def reset(self, price: float | None = None) -> None:
        """Reset price to initial (or specified) level."""
        self._price = price if price is not None else self._cfg.initial_price
        self._tick_count = 0
