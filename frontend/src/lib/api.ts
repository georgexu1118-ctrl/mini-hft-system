/**
 * REST API client — thin wrappers over fetch.
 * All paths go through Next.js rewrites in development (→ localhost:8000).
 */

import type {
  OrderRequest,
  OrderResponse,
  MarketMakerRequest,
} from "./types";

const BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...init?.headers },
    ...init,
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${body}`);
  }
  return res.json() as Promise<T>;
}

// ── Orders ────────────────────────────────────────────────────────────────────

export function submitOrder(req: OrderRequest): Promise<OrderResponse> {
  return request("/api/v1/orders/", {
    method: "POST",
    body: JSON.stringify(req),
  });
}

export function cancelOrder(orderId: string): Promise<{ order_id: string; status: string }> {
  return request(`/api/v1/orders/${orderId}`, { method: "DELETE" });
}

export function getOrder(orderId: string): Promise<OrderResponse> {
  return request(`/api/v1/orders/${orderId}`);
}

// ── Market data ───────────────────────────────────────────────────────────────

export function getSymbols(): Promise<{ symbols: string[] }> {
  return request("/api/v1/market-data/symbols");
}

export function getSnapshot(symbol: string, depth = 10) {
  return request(`/api/v1/market-data/${symbol}/snapshot?depth=${depth}`);
}

// ── Metrics ───────────────────────────────────────────────────────────────────

export function getMetrics() {
  return request("/api/v1/metrics/");
}

// ── Strategies ────────────────────────────────────────────────────────────────

export function listStrategies(): Promise<{ strategies: Record<string, Record<string, unknown>> }> {
  return request("/api/v1/strategies/");
}

export function createMarketMaker(req: MarketMakerRequest): Promise<{ strategy_id: string; status: string }> {
  return request("/api/v1/strategies/market-makers", {
    method: "POST",
    body: JSON.stringify(req),
  });
}

export function killStrategy(strategyId: string): Promise<{ strategy_id: string; status: string }> {
  return request(`/api/v1/strategies/${strategyId}/kill`, { method: "POST" });
}

// ── Health ────────────────────────────────────────────────────────────────────

export function getHealth() {
  return request("/health");
}
