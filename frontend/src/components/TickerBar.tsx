/**
 * Ticker bar — top of the dashboard showing current BBO and last trade.
 */

"use client";

import type { BookState, TickerMessage } from "@/lib/types";
import clsx from "clsx";

interface Props {
  symbol: string;
  book: BookState | null;
  ticker: TickerMessage | null;
}

function bestBid(book: BookState | null): number | null {
  if (!book || book.bids.size === 0) return null;
  return Math.max(...book.bids.keys());
}

function bestAsk(book: BookState | null): number | null {
  if (!book || book.asks.size === 0) return null;
  return Math.min(...book.asks.keys());
}

function fmt(n: number | null | undefined, d = 2) {
  if (n == null) return "—";
  return n.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
}

export default function TickerBar({ symbol, book, ticker }: Props) {
  return (
    <div className="flex items-center gap-6 text-sm">
      <span className="font-bold text-terminal-accent text-base">{symbol}</span>

      <TickItem label="BID" value={fmt(bestBid(book))} color="text-bid" />
      <TickItem label="ASK" value={fmt(bestAsk(book))} color="text-ask" />
      <TickItem label="LAST" value={fmt(ticker?.last ?? null)} color="text-trade" />
      <TickItem label="SPRD" value={book?.spread != null ? fmt(book.spread) : "—"} />
      <TickItem label="VOL" value={ticker?.volume?.toLocaleString() ?? "—"} />
    </div>
  );
}

function TickItem({ label, value, color = "text-terminal-text" }: { label: string; value: string; color?: string }) {
  return (
    <span className="flex flex-col items-center leading-none">
      <span className="text-[9px] text-terminal-muted">{label}</span>
      <span className={clsx("font-semibold text-xs", color)}>{value}</span>
    </span>
  );
}
