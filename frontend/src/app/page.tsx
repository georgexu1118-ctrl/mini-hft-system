/**
 * Mini HFT Dashboard — main page
 *
 * Layout (dark trading terminal):
 * ┌────────────────────────────────────────────────────────────┐
 * │  Header: symbol ticker + system health                      │
 * ├──────────────┬──────────────────────┬──────────────────────┤
 * │ Order Book   │   Latency Chart      │ Order Entry          │
 * │ (10 levels)  │                      │                      │
 * │              ├──────────────────────┤ Strategy Panel       │
 * │              │   Trade Tape         │                      │
 * └──────────────┴──────────────────────┴──────────────────────┘
 */

"use client";

import { useState } from "react";
import { useMarketData } from "@/hooks/useMarketData";
import OrderBook from "@/components/OrderBook";
import TradeTape from "@/components/TradeTape";
import LatencyChart from "@/components/LatencyChart";
import OrderEntry from "@/components/OrderEntry";
import StrategyPanel from "@/components/StrategyPanel";
import TickerBar from "@/components/TickerBar";
import SystemHealth from "@/components/SystemHealth";

const SYMBOLS = ["AAPL", "TSLA", "MSFT", "AMZN", "NVDA"];

export default function Dashboard() {
  const [symbol, setSymbol] = useState("AAPL");
  const {
    connected,
    book,
    trades,
    ticker,
    latency,
    latencyHistory,
    strategyMetrics,
    health,
    lastHeartbeat,
  } = useMarketData(symbol);

  return (
    <div className="flex flex-col h-screen bg-terminal-bg text-terminal-text overflow-hidden">

      {/* ── Header ─────────────────────────────────────────────────────────── */}
      <header className="flex items-center justify-between px-4 py-2 border-b border-terminal-border bg-terminal-surface shrink-0">
        <div className="flex items-center gap-4">
          <span className="text-terminal-accent font-bold text-sm tracking-widest">
            MINI HFT
          </span>

          {/* Symbol selector */}
          <div className="flex gap-1">
            {SYMBOLS.map((s) => (
              <button
                key={s}
                onClick={() => setSymbol(s)}
                className={
                  s === symbol
                    ? "px-2 py-0.5 rounded bg-terminal-accent/20 text-terminal-accent border border-terminal-accent/50 text-xs font-semibold"
                    : "px-2 py-0.5 rounded text-terminal-muted hover:text-terminal-text text-xs transition-colors"
                }
              >
                {s}
              </button>
            ))}
          </div>

          <TickerBar symbol={symbol} book={book} ticker={ticker} />
        </div>

        <SystemHealth health={health} connected={connected} lastHeartbeat={lastHeartbeat} />
      </header>

      {/* ── Main grid ──────────────────────────────────────────────────────── */}
      <main className="flex-1 grid grid-cols-[260px_1fr_280px] gap-0 overflow-hidden min-h-0">

        {/* ── Col 1: Order Book ──────────────────────────────────────────── */}
        <section className="flex flex-col border-r border-terminal-border overflow-hidden">
          <SectionHeader>Order Book</SectionHeader>
          <div className="flex-1 overflow-y-auto p-2">
            <OrderBook book={book} depth={10} />
          </div>
        </section>

        {/* ── Col 2: Latency + Trade Tape ────────────────────────────────── */}
        <section className="flex flex-col overflow-hidden">
          {/* Latency monitor — top half */}
          <div className="flex flex-col border-b border-terminal-border" style={{ height: "45%" }}>
            <SectionHeader>Latency Monitor</SectionHeader>
            <div className="flex-1 min-h-0 p-3">
              <LatencyChart history={latencyHistory} current={latency} />
            </div>
          </div>

          {/* Trade Tape — bottom half */}
          <div className="flex flex-col flex-1 min-h-0">
            <SectionHeader>
              Trade Tape
              <span className="ml-2 text-terminal-muted text-[10px] font-normal">
                ({trades.length} prints)
              </span>
            </SectionHeader>
            <div className="flex-1 overflow-hidden">
              <TradeTape trades={trades} />
            </div>
          </div>
        </section>

        {/* ── Col 3: Order Entry + Strategy ──────────────────────────────── */}
        <section className="flex flex-col border-l border-terminal-border overflow-hidden">
          {/* Order entry — top */}
          <div className="border-b border-terminal-border">
            <SectionHeader>Order Entry</SectionHeader>
            <div className="p-3">
              <OrderEntry symbol={symbol} midPrice={book?.mid_price ?? null} />
            </div>
          </div>

          {/* Strategy panel — bottom */}
          <div className="flex flex-col flex-1 min-h-0 overflow-y-auto">
            <SectionHeader>Strategies</SectionHeader>
            <div className="p-3">
              <StrategyPanel symbol={symbol} metrics={strategyMetrics} />
            </div>
          </div>
        </section>
      </main>
    </div>
  );
}

function SectionHeader({ children }: { children: React.ReactNode }) {
  return (
    <div className="px-3 py-1.5 bg-terminal-surface border-b border-terminal-border text-[11px] font-semibold text-terminal-muted uppercase tracking-wider shrink-0">
      {children}
    </div>
  );
}
