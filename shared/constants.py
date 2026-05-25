"""
System-wide constants. Import from here — never hardcode in business logic.

Keeping constants centralised means a single diff when tuning parameters.
In production these would come from a configuration management system
(etcd, Consul, or Vault for secrets) so they can be changed without deploys.
"""

# Default tradeable instruments registered at startup
DEFAULT_SYMBOLS: list[str] = ["AAPL", "GOOGL", "MSFT", "TSLA", "SPY"]

# Order book snapshot depth sent on each update
DEFAULT_BOOK_DEPTH: int = 10

# Engine event queue size. QueueFull → drop + increment counter.
# Size: 10k events at ~200 bytes each ≈ 2 MB of pending events.
EVENT_QUEUE_DEPTH: int = 10_000

# Market data feed frequency (seconds between ticks per symbol)
# 0.1s = 10 Hz. Enough to demo real-time updates without flooding the WS.
MARKET_DATA_TICK_INTERVAL: float = 0.1

# Heartbeat interval for WebSocket keep-alive (seconds)
WEBSOCKET_HEARTBEAT_INTERVAL: int = 30

# Hard limits for pre-trade risk checks
MAX_ORDER_QUANTITY: int = 1_000_000
MIN_ORDER_PRICE: float = 0.01
MAX_ORDER_PRICE: float = 1_000_000.0

# Default GBM parameters for synthetic market data
DEFAULT_INITIAL_PRICE: float = 100.0
DEFAULT_TICK_VOLATILITY: float = 0.0002   # per-tick vol (roughly 0.02%)
DEFAULT_BID_ASK_SPREAD: float = 0.02      # $0.02 spread
DEFAULT_BASE_VOLUME: int = 200            # median trade size (log-normal)
