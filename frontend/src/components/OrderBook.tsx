/**
 * Order Book Ladder
 *
 * Renders the 10-level bid/ask depth visualisation. Bids = green,
 * asks = red. Bar width represents relative volume at each level.
 * Spread and mid-price displayed between the sides.
 */

"use client";

import { useMemo } from "react";
import type { BookState } from "@/lib/types";
import clsx from "clsx";

interface Props {
  book: BookState | null;
  depth?: number;
}

function fmt(n: number, decimals = 2) {
  return n.toLocaleString("en-US", { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}

export default function OrderBook({ book, depth = 10 }: Props) {
  const { bids, asks, maxQty } = useMemo(() => {
    if (!book) return { bids: [], asks: [], maxQty: 1 };

    const sortedBids = [...book.bids.values()]
      .sort((a, b) => b.price - a.price)
      .slice(0, depth);
    const sortedAsks = [...book.asks.values()]
      .sort((a, b) => a.price - b.price)
      .slice(0, depth);

    const all = [...sortedBids, ...sortedAsks];
    const maxQty = all.reduce((m, l) => Math.max(m, l.quantity), 1);

    return { bids: sortedBids, asks: sortedAsks, maxQty };
  }, [book, depth]);

  if (!book) {
    return (
      <div className="flex items-center justify-center h-64 text-terminal-muted text-sm">
        Waiting for book snapshot…
      </div>
    );
  }

  return (
    <div className="select-none">
      {/* Header */}
      <div className="grid grid-cols-3 text-xs text-terminal-muted mb-1 px-1">
        <span>ORDERS</span>
        <span className="text-center">QTY</span>
        <span className="text-right">PRICE</span>
      </div>

      {/* Asks (reversed so best ask is closest to spread) */}
      <div className="flex flex-col-reverse">
        {asks.map((level) => (
          <LevelRow
            key={level.price}
            level={level}
            maxQty={maxQty}
            side="ask"
          />
        ))}
      </div>

      {/* Spread line */}
      <div className="flex items-center justify-between px-1 py-1 border-y border-terminal-border text-xs my-0.5">
        <span className="text-terminal-muted">
          SPRD <span className="text-terminal-text">{book.spread != null ? fmt(book.spread) : "—"}</span>
        </span>
        <span className="text-terminal-muted">
          MID <span className="text-terminal-accent">{book.mid_price != null ? fmt(book.mid_price) : "—"}</span>
        </span>
        <span className="text-terminal-muted">
          SEQ <span className="text-terminal-text">{book.sequence}</span>
        </span>
      </div>

      {/* Bids */}
      {bids.map((level) => (
        <LevelRow
          key={level.price}
          level={level}
          maxQty={maxQty}
          side="bid"
        />
      ))}
    </div>
  );
}

function LevelRow({
  level,
  maxQty,
  side,
}: {
  level: { price: number; quantity: number; order_count: number };
  maxQty: number;
  side: "bid" | "ask";
}) {
  const pct = (level.quantity / maxQty) * 100;
  const isBid = side === "bid";

  return (
    <div className="relative grid grid-cols-3 text-xs px-1 py-[2px] hover:brightness-125 transition-colors">
      {/* Volume bar */}
      <div
        className={clsx(
          "absolute inset-y-0 opacity-20",
          isBid ? "bg-bid right-0" : "bg-ask left-0"
        )}
        style={isBid ? { width: `${pct}%`, right: 0 } : { width: `${pct}%`, left: 0 }}
      />

      {/* Order count */}
      <span className="relative text-terminal-muted">{level.order_count}</span>

      {/* Quantity */}
      <span className={clsx("relative text-center", isBid ? "text-bid" : "text-ask")}>
        {level.quantity.toLocaleString()}
      </span>

      {/* Price */}
      <span className={clsx("relative text-right font-semibold", isBid ? "text-bid" : "text-ask")}>
        {fmt(level.price)}
      </span>
    </div>
  );
}
