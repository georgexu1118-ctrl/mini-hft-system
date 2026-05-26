/**
 * Central WebSocket hook.
 *
 * Connects to /api/v1/market-data/ws, subscribes to a symbol, and
 * maintains local state for:
 *   - Book (snapshot + delta patching)
 *   - Trade tape
 *   - Latency history
 *   - Strategy metrics
 *   - System health
 *
 * Reconnects automatically with exponential backoff (1 → 2 → 4 → 8 → 16 s).
 */

"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type {
  BookLevel,
  BookState,
  LatencyPoint,
  LatencySnapshotMessage,
  OrderAckMessage,
  PnlMessage,
  StrategyMetricsMessage,
  SystemHealthMessage,
  TickerMessage,
  TradeMessage,
  WSMessage,
} from "@/lib/types";

const WS_BASE =
  typeof window !== "undefined"
    ? (process.env.NEXT_PUBLIC_WS_URL ?? `ws://${window.location.hostname}:8000`)
    : "ws://localhost:8000";

const MAX_TRADES = 50;
const MAX_LATENCY_POINTS = 120; // 2 minutes at 1s push interval

// ── Book patch helpers ────────────────────────────────────────────────────────

function applySnapshot(
  prev: BookState | null,
  msg: { symbol: string; sequence: number; bids: BookLevel[]; asks: BookLevel[]; spread: number | null; mid_price: number | null; timestamp_ns: number }
): BookState {
  const bids = new Map<number, BookLevel>(msg.bids.map((l) => [l.price, l]));
  const asks = new Map<number, BookLevel>(msg.asks.map((l) => [l.price, l]));
  return {
    symbol: msg.symbol,
    sequence: msg.sequence,
    bids,
    asks,
    spread: msg.spread,
    mid_price: msg.mid_price,
    lastUpdated: Date.now(),
  };
}

function applyDelta(prev: BookState, msg: { sequence: number; bids: BookLevel[]; asks: BookLevel[]; spread: number | null; mid_price: number | null }): BookState {
  const bids = new Map(prev.bids);
  const asks = new Map(prev.asks);

  for (const l of msg.bids) {
    if (l.quantity === 0) bids.delete(l.price);
    else bids.set(l.price, l);
  }
  for (const l of msg.asks) {
    if (l.quantity === 0) asks.delete(l.price);
    else asks.set(l.price, l);
  }

  return {
    ...prev,
    sequence: msg.sequence,
    bids,
    asks,
    spread: msg.spread,
    mid_price: msg.mid_price,
    lastUpdated: Date.now(),
  };
}

// ── Hook ──────────────────────────────────────────────────────────────────────

export interface MarketDataState {
  connected: boolean;
  book: BookState | null;
  trades: TradeMessage[];
  ticker: TickerMessage | null;
  latency: LatencySnapshotMessage | null;
  latencyHistory: LatencyPoint[];
  orderAcks: OrderAckMessage[];
  strategyMetrics: StrategyMetricsMessage | null;
  pnl: PnlMessage | null;
  health: SystemHealthMessage | null;
  lastHeartbeat: number | null;
}

export function useMarketData(symbol: string): MarketDataState {
  const [state, setState] = useState<MarketDataState>({
    connected: false,
    book: null,
    trades: [],
    ticker: null,
    latency: null,
    latencyHistory: [],
    orderAcks: [],
    strategyMetrics: null,
    pnl: null,
    health: null,
    lastHeartbeat: null,
  });

  const wsRef = useRef<WebSocket | null>(null);
  const backoffRef = useRef(1_000);
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mountedRef = useRef(true);

  const connect = useCallback(() => {
    const url = `${WS_BASE}/api/v1/market-data/ws`;
    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      backoffRef.current = 1_000;
      // Subscribe to our symbol + global feeds
      ws.send(JSON.stringify({ action: "subscribe", symbol }));
      ws.send(JSON.stringify({ action: "subscribe_all" }));
      setState((s) => ({ ...s, connected: true }));
    };

    ws.onmessage = (ev) => {
      let msg: WSMessage;
      try {
        msg = JSON.parse(ev.data as string) as WSMessage;
      } catch {
        return;
      }

      setState((s) => {
        switch (msg.type) {
          case "BOOK_SNAPSHOT":
            return { ...s, book: applySnapshot(s.book, msg) };

          case "BOOK_DELTA": {
            if (!s.book) return s; // need snapshot first
            if (msg.previous_sequence !== s.book.sequence) {
              // Gap detected — we'll get a new snapshot shortly from server
              return s;
            }
            return { ...s, book: applyDelta(s.book, msg) };
          }

          case "TRADE":
            return {
              ...s,
              trades: [msg, ...s.trades].slice(0, MAX_TRADES),
            };

          case "TICKER":
            return { ...s, ticker: msg };

          case "ORDER_ACK":
            return {
              ...s,
              orderAcks: [msg, ...s.orderAcks].slice(0, 20),
            };

          case "LATENCY_SNAPSHOT": {
            const point: LatencyPoint = {
              t: Date.now(),
              p50: msg.p50_us ?? 0,
              p99: msg.p99_us ?? 0,
              p999: msg.p999_us ?? 0,
            };
            return {
              ...s,
              latency: msg,
              latencyHistory: [...s.latencyHistory, point].slice(-MAX_LATENCY_POINTS),
            };
          }

          case "STRATEGY_METRICS":
            return { ...s, strategyMetrics: msg };

          case "PNL":
            return { ...s, pnl: msg };

          case "SYSTEM_HEALTH":
            return { ...s, health: msg };

          case "HEARTBEAT":
            return { ...s, lastHeartbeat: Date.now() };

          default:
            return s;
        }
      });
    };

    ws.onclose = () => {
      if (!mountedRef.current) return;
      setState((s) => ({ ...s, connected: false }));
      retryTimerRef.current = setTimeout(() => {
        backoffRef.current = Math.min(backoffRef.current * 2, 16_000);
        connect();
      }, backoffRef.current);
    };

    ws.onerror = () => {
      ws.close();
    };
  }, [symbol]);

  useEffect(() => {
    mountedRef.current = true;
    connect();
    return () => {
      mountedRef.current = false;
      if (retryTimerRef.current) clearTimeout(retryTimerRef.current);
      wsRef.current?.close();
    };
  }, [connect]);

  return state;
}
