/**
 * Trade Tape (Time & Sales)
 *
 * Scrolling list of recent executions — price, size, direction.
 * New trades flash the row. Colour-coded by aggressor side.
 */

"use client";

import type { TradeMessage } from "@/lib/types";
import clsx from "clsx";

interface Props {
  trades: TradeMessage[];
}

function fmt(n: number, d = 2) {
  return n.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
}

function fmtTime(ns: number) {
  const ms = ns / 1_000_000;
  return new Date(ms).toLocaleTimeString("en-US", {
    hour12: false,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export default function TradeTape({ trades }: Props) {
  return (
    <div className="overflow-y-auto h-full">
      {/* Column headers */}
      <div className="sticky top-0 bg-terminal-surface grid grid-cols-4 text-xs text-terminal-muted px-2 py-1 border-b border-terminal-border">
        <span>TIME</span>
        <span className="text-center">SIDE</span>
        <span className="text-right">PRICE</span>
        <span className="text-right">QTY</span>
      </div>

      {trades.length === 0 && (
        <div className="text-center text-terminal-muted text-xs py-8">
          No trades yet
        </div>
      )}

      {trades.map((t, i) => (
        <div
          key={t.trade_id}
          className={clsx(
            "grid grid-cols-4 text-xs px-2 py-[3px] border-b border-terminal-border/40",
            "hover:bg-terminal-border/20 transition-colors",
            i === 0 && (t.aggressor_side === "BUY" ? "flash-green" : "flash-red")
          )}
        >
          <span className="text-terminal-muted">{fmtTime(t.timestamp_ns)}</span>
          <span
            className={clsx(
              "text-center font-semibold",
              t.aggressor_side === "BUY" ? "text-bid" : "text-ask"
            )}
          >
            {t.aggressor_side === "BUY" ? "▲" : "▼"} {t.aggressor_side}
          </span>
          <span
            className={clsx(
              "text-right font-semibold",
              t.aggressor_side === "BUY" ? "text-bid" : "text-ask"
            )}
          >
            {fmt(t.price)}
          </span>
          <span className="text-right text-terminal-text">{t.quantity.toLocaleString()}</span>
        </div>
      ))}
    </div>
  );
}
