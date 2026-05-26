"""
Transport layer abstractions — order ingress and market data egress.

The transport layer is the outermost boundary of the system: where orders
arrive and where market data leaves. Making this boundary abstract allows
swapping between:

  Ingress channels:
  - REST/JSON (current)         — universal, high latency (ms)
  - Binary UDP (near-term)      — low overhead, sub-ms
  - FIX protocol                — industry standard, many brokers
  - Direct memory (FPGA path)   — sub-µs, used for co-located strategies

  Egress channels:
  - WebSocket/JSON (current)    — browser clients, dashboards
  - Binary UDP multicast        — institutional feed distribution
  - SBE (Simple Binary Encoding) — CME/exchange compatible format
  - FPGA DMA completion ring    — downstream FPGA strategy processor

FPGA feed handler architecture
────────────────────────────────
In production HFT, the market data feed handler is the second FPGA
acceleration target (after the matching engine itself):

  Network card (FPGA NIC or SmartNIC)
      │ Raw UDP multicast packets (ITCH 5.0 / MDP 3.0)
      │ 100 Gbps optical fiber
      ▼
  FPGA Feed Parser
      │ Parse binary packet headers in hardware
      │ Extract message type, symbol, price, qty
      │ Write decoded BookUpdate struct to host memory via PCIe DMA
      │ Zero CPU involvement up to this point
      ▼
  Host DMA Ring (in hugepage memory)
      │ CPU polls completion counter
      ▼
  FeedHandlerBase.on_update() called from polling loop
      │ Update internal state, emit TickerEvent to EventBus
      ▼
  EventBus → WS broadcaster, strategy executor

Key property: the FPGA absorbs all packet parsing jitter. The CPU only
sees clean, decoded messages. This eliminates the multi-microsecond
latency spikes caused by OS network stack processing.
"""
from __future__ import annotations
from abc import ABC, abstractmethod

from engine.core.order import Order, Trade
from engine.core.order_book import BookSnapshot
from engine.core.types import Symbol


class OrderIngress(ABC):
    """
    Abstract order entry channel.

    Each implementation represents a different order entry protocol.
    The matching engine and OrderService are independent of which
    channel delivered the order.

    Current:    HTTP REST (FastAPI router calls OrderService directly)
    Near-term:  Binary UDP listener (FIX, OUCH, or native binary)
    FPGA path:  Memory-mapped write by co-located strategy — the strategy
                writes an ORDER_FRAME to the host hugepage and the HAL
                submits it without any network round-trip

    Note: the current REST architecture doesn't use this class yet —
    orders flow: Router → OrderService → HAL. This ABC is the stepping
    stone for adding non-REST ingress channels in M7/M8.
    """

    @abstractmethod
    async def start(self) -> None:
        """Begin accepting orders. Runs until stop() is called."""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Graceful shutdown."""
        ...

    @abstractmethod
    def on_order(self, order: Order) -> None:
        """
        Called when a new order arrives on this channel.
        Implementations route to OrderService.
        """
        ...


class FeedHandlerBase(ABC):
    """
    Abstract market data feed consumer.

    Parses raw feed data and calls on_update() with decoded updates.
    The parsing and the update delivery are separated so that the FPGA
    can own the parsing (hardware) while the CPU handles on_update() (software).

    Nasdaq ITCH 5.0 message types the FPGA would parse:
      0x41 'A' — Add Order (no MPID)
      0x46 'F' — Add Order (with MPID)
      0x44 'D' — Delete Order
      0x45 'E' — Order Executed
      0x50 'P' — Trade (non-cross)
      ...

    Each message is 20–40 bytes. At peak (NYSE open), 5M+ messages/sec
    arrive on the wire. No Python code runs fast enough to parse that
    inline — hardware parsing is mandatory.
    """

    @abstractmethod
    async def run(self) -> None:
        """Main loop. Runs until cancelled (asyncio.CancelledError)."""
        ...

    @abstractmethod
    def on_book_update(self, symbol: Symbol, snapshot: BookSnapshot) -> None:
        """Called when a book update is fully parsed. Emit TickerEvent."""
        ...

    @abstractmethod
    def on_trade(self, trade: Trade) -> None:
        """Called when a trade print is parsed from the feed."""
        ...

    @abstractmethod
    def get_current_price(self, symbol: Symbol) -> float | None:
        """Best estimate of current mid-price for the symbol."""
        ...

    @property
    @abstractmethod
    def is_running(self) -> bool:
        ...

    @property
    def feed_type(self) -> str:
        """Human-readable name of this feed (e.g. 'ITCH5.0', 'GBM-synthetic')."""
        return self.__class__.__name__


class MarketDataEgress(ABC):
    """
    Abstract market data dissemination channel.

    Current: WebSocket JSON broadcast (in api/websocket/manager.py).
    Future:  SBE binary multicast, or FPGA-side broadcast for co-located clients.
    """

    @abstractmethod
    async def publish_snapshot(self, snapshot: BookSnapshot) -> None:
        ...

    @abstractmethod
    async def publish_trade(self, trade: Trade) -> None:
        ...
