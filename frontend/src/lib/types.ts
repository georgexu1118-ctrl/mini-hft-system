/**
 * TypeScript types matching the FastAPI WebSocket schemas exactly.
 * Keep in sync with api/websocket/schemas.py.
 */

export interface BookLevel {
  price: number;
  quantity: number;
  order_count: number;
}

export interface BookSnapshotMessage {
  type: "BOOK_SNAPSHOT";
  symbol: string;
  sequence: number;
  bids: BookLevel[];
  asks: BookLevel[];
  spread: number | null;
  mid_price: number | null;
  timestamp_ns: number;
}

export interface BookDeltaMessage {
  type: "BOOK_DELTA";
  symbol: string;
  previous_sequence: number;
  sequence: number;
  bids: BookLevel[];  // quantity=0 means delete this level
  asks: BookLevel[];
  spread: number | null;
  mid_price: number | null;
  timestamp_ns: number;
}

export interface TradeMessage {
  type: "TRADE";
  trade_id: string;
  symbol: string;
  price: number;
  quantity: number;
  notional: number;
  aggressor_side: "BUY" | "SELL";
  maker_order_id: string;
  taker_order_id: string;
  timestamp_ns: number;
}

export interface OrderAckMessage {
  type: "ORDER_ACK";
  order_id: string;
  client_order_id: string | null;
  symbol: string;
  side: "BUY" | "SELL";
  status: "PENDING" | "OPEN" | "PARTIALLY_FILLED" | "FILLED" | "CANCELLED" | "REJECTED";
  quantity: number;
  filled_quantity: number;
  remaining_quantity: number;
  avg_fill_price: number | null;
  trades_count: number;
  gateway_latency_us: number | null;
  processing_latency_us: number | null;
  roundtrip_latency_us: number | null;
  timestamp_ns: number;
}

export interface TickerMessage {
  type: "TICKER";
  symbol: string;
  bid: number;
  ask: number;
  last: number;
  volume: number;
  timestamp_ns: number;
}

export interface LatencySnapshotMessage {
  type: "LATENCY_SNAPSHOT";
  p50_us: number | null;
  p99_us: number | null;
  p999_us: number | null;
  orders_received: number;
  trades_executed: number;
  total_volume: number;
  dropped_events: number;
  timestamp_ns: number;
}

export interface StrategyMetricsMessage {
  type: "STRATEGY_METRICS";
  strategies: Record<string, Record<string, number | string | null>>;
  timestamp_ns: number;
}

export interface PnlMessage {
  type: "PNL";
  strategies: Record<string, Record<string, number | string | null>>;
  timestamp_ns: number;
}

export interface SystemHealthMessage {
  type: "SYSTEM_HEALTH";
  status: "OK" | "DEGRADED";
  event_bus_drops: number;
  persistence_drops: number;
  persistence_failed_batches: number;
  strategy_callback_failures: number;
  timestamp_ns: number;
}

export interface HeartbeatMessage {
  type: "HEARTBEAT";
  timestamp_ns: number;
}

export interface ErrorMessage {
  type: "ERROR";
  code: string;
  message: string;
  timestamp_ns: number;
}

export type WSMessage =
  | BookSnapshotMessage
  | BookDeltaMessage
  | TradeMessage
  | OrderAckMessage
  | TickerMessage
  | LatencySnapshotMessage
  | StrategyMetricsMessage
  | PnlMessage
  | SystemHealthMessage
  | HeartbeatMessage
  | ErrorMessage;

// ── REST API types ────────────────────────────────────────────────────────────

export type OrderSide = "BUY" | "SELL";
export type OrderType = "LIMIT" | "MARKET";
export type TimeInForce = "GTC" | "IOC" | "FOK" | "GTD";

export interface OrderRequest {
  symbol: string;
  side: OrderSide;
  order_type: OrderType;
  quantity: number;
  price?: number;
  time_in_force?: TimeInForce;
  client_order_id?: string;
}

export interface TradeResponse {
  trade_id: string;
  price: number;
  quantity: number;
  notional: number;
  aggressor_side: string;
  maker_order_id: string;
  taker_order_id: string;
  timestamp_ns: number;
}

export interface OrderResponse {
  order_id: string;
  client_order_id: string | null;
  symbol: string;
  side: string;
  order_type: string;
  quantity: number;
  price: number | null;
  status: string;
  filled_quantity: number;
  remaining_quantity: number;
  avg_fill_price: number | null;
  trades: TradeResponse[];
  gateway_latency_us: number | null;
  processing_latency_us: number | null;
  roundtrip_latency_us: number | null;
}

export interface MarketMakerRequest {
  strategy_id: string;
  symbol: string;
  spread_ticks: number;
  quote_size: number;
  inventory_skew_ticks: number;
  max_position: number;
  max_order_notional: number;
}

// ── Local state shapes ────────────────────────────────────────────────────────

/** Book state maintained client-side by applying snapshots + deltas */
export interface BookState {
  symbol: string;
  sequence: number;
  bids: Map<number, BookLevel>;   // price → level
  asks: Map<number, BookLevel>;
  spread: number | null;
  mid_price: number | null;
  lastUpdated: number;
}

/** A single latency data point for charting */
export interface LatencyPoint {
  t: number;   // timestamp (ms epoch)
  p50: number;
  p99: number;
  p999: number;
}
