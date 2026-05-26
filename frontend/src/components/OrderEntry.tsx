/**
 * Order Entry form
 *
 * Submits LIMIT or MARKET orders to POST /api/v1/orders/.
 * Shows the full latency breakdown on each response — the educational
 * centrepiece of the simulator.
 */

"use client";

import { useState } from "react";
import { submitOrder } from "@/lib/api";
import type { OrderResponse, OrderSide, OrderType, TimeInForce } from "@/lib/types";
import clsx from "clsx";

interface Props {
  symbol: string;
  midPrice: number | null;
}

function fmt(v: number | null | undefined) {
  if (v == null) return "—";
  if (v >= 1000) return `${(v / 1000).toFixed(2)} ms`;
  return `${v.toFixed(1)} µs`;
}

export default function OrderEntry({ symbol, midPrice }: Props) {
  const [side, setSide] = useState<OrderSide>("BUY");
  const [orderType, setOrderType] = useState<OrderType>("LIMIT");
  const [qty, setQty] = useState("100");
  const [price, setPrice] = useState("");
  const [tif, setTif] = useState<TimeInForce>("GTC");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<OrderResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setResult(null);
    setLoading(true);

    try {
      const res = await submitOrder({
        symbol,
        side,
        order_type: orderType,
        quantity: parseInt(qty, 10),
        price: orderType === "LIMIT" ? parseFloat(price) : undefined,
        time_in_force: tif,
      });
      setResult(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  };

  // Prefill price when user focuses price field with mid-price
  const handlePriceFocus = () => {
    if (!price && midPrice) setPrice(midPrice.toFixed(2));
  };

  return (
    <div className="flex flex-col gap-3">
      <form onSubmit={handleSubmit} className="flex flex-col gap-3">
        {/* BUY / SELL toggle */}
        <div className="grid grid-cols-2 gap-1">
          <button
            type="button"
            onClick={() => setSide("BUY")}
            className={clsx(
              "py-2 rounded text-sm font-semibold transition-colors",
              side === "BUY"
                ? "bg-bid text-white"
                : "bg-terminal-border text-terminal-muted hover:bg-bid/20"
            )}
          >
            BUY
          </button>
          <button
            type="button"
            onClick={() => setSide("SELL")}
            className={clsx(
              "py-2 rounded text-sm font-semibold transition-colors",
              side === "SELL"
                ? "bg-ask text-white"
                : "bg-terminal-border text-terminal-muted hover:bg-ask/20"
            )}
          >
            SELL
          </button>
        </div>

        {/* Order type */}
        <div className="grid grid-cols-2 gap-1">
          {(["LIMIT", "MARKET"] as OrderType[]).map((t) => (
            <button
              key={t}
              type="button"
              onClick={() => setOrderType(t)}
              className={clsx(
                "py-1 text-xs rounded transition-colors",
                orderType === t
                  ? "bg-terminal-accent/20 text-terminal-accent border border-terminal-accent/50"
                  : "bg-terminal-border text-terminal-muted hover:text-terminal-text"
              )}
            >
              {t}
            </button>
          ))}
        </div>

        {/* Quantity */}
        <label className="flex flex-col gap-1">
          <span className="text-xs text-terminal-muted">Quantity</span>
          <input
            type="number"
            min={1}
            value={qty}
            onChange={(e) => setQty(e.target.value)}
            className="bg-terminal-bg border border-terminal-border rounded px-2 py-1.5 text-sm text-terminal-text focus:border-terminal-accent outline-none"
            required
          />
        </label>

        {/* Price (limit only) */}
        {orderType === "LIMIT" && (
          <label className="flex flex-col gap-1">
            <span className="text-xs text-terminal-muted">
              Limit Price {midPrice && <span className="text-terminal-accent/70">(mid: {midPrice.toFixed(2)})</span>}
            </span>
            <input
              type="number"
              step="0.01"
              value={price}
              onChange={(e) => setPrice(e.target.value)}
              onFocus={handlePriceFocus}
              className="bg-terminal-bg border border-terminal-border rounded px-2 py-1.5 text-sm text-terminal-text focus:border-terminal-accent outline-none"
              required
            />
          </label>
        )}

        {/* TIF (limit only) */}
        {orderType === "LIMIT" && (
          <label className="flex flex-col gap-1">
            <span className="text-xs text-terminal-muted">Time in Force</span>
            <select
              value={tif}
              onChange={(e) => setTif(e.target.value as TimeInForce)}
              className="bg-terminal-bg border border-terminal-border rounded px-2 py-1.5 text-sm text-terminal-text focus:border-terminal-accent outline-none"
            >
              <option value="GTC">GTC — Good Till Cancel</option>
              <option value="IOC">IOC — Immediate or Cancel</option>
              <option value="FOK">FOK — Fill or Kill</option>
            </select>
          </label>
        )}

        <button
          type="submit"
          disabled={loading}
          className={clsx(
            "py-2 rounded text-sm font-semibold transition-colors",
            side === "BUY"
              ? "bg-bid hover:bg-bid/80 text-white"
              : "bg-ask hover:bg-ask/80 text-white",
            loading && "opacity-50 cursor-not-allowed"
          )}
        >
          {loading ? "Sending…" : `${side} ${qty} ${symbol}`}
        </button>
      </form>

      {/* Error */}
      {error && (
        <div className="text-xs text-ask bg-ask/10 border border-ask/30 rounded p-2">
          {error}
        </div>
      )}

      {/* Result */}
      {result && (
        <div className="text-xs bg-terminal-bg border border-terminal-border rounded p-2 space-y-1">
          <div className="flex justify-between">
            <span className="text-terminal-muted">Status</span>
            <span className={clsx(
              "font-semibold",
              result.status === "FILLED" ? "text-bid" :
              result.status === "CANCELLED" ? "text-ask" :
              "text-trade"
            )}>{result.status}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-terminal-muted">Filled</span>
            <span>{result.filled_quantity} / {result.quantity}</span>
          </div>
          {result.avg_fill_price && (
            <div className="flex justify-between">
              <span className="text-terminal-muted">Avg Fill</span>
              <span className="text-bid">{result.avg_fill_price.toFixed(2)}</span>
            </div>
          )}
          {result.trades.length > 0 && (
            <div className="flex justify-between">
              <span className="text-terminal-muted">Trades</span>
              <span>{result.trades.length}</span>
            </div>
          )}

          {/* Latency breakdown — the star of the show */}
          <div className="mt-2 pt-2 border-t border-terminal-border space-y-0.5">
            <div className="text-terminal-muted text-[10px] uppercase mb-1">Latency Breakdown</div>
            <LatencyRow label="Gateway" value={result.gateway_latency_us} />
            <LatencyRow label="Matching" value={result.processing_latency_us} />
            <LatencyRow label="Round-trip" value={result.roundtrip_latency_us} />
          </div>
        </div>
      )}
    </div>
  );
}

function LatencyRow({ label, value }: { label: string; value: number | null | undefined }) {
  return (
    <div className="flex justify-between text-[11px]">
      <span className="text-terminal-muted">{label}</span>
      <span className={clsx(
        value != null && value < 100 ? "text-bid" :
        value != null && value < 1000 ? "text-trade" :
        "text-ask"
      )}>
        {fmt(value)}
      </span>
    </div>
  );
}
