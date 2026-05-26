/**
 * Strategy Panel
 *
 * Controls for launching and killing the SimpleMarketMaker strategy.
 * Displays PnL, position, and fill metrics per strategy.
 */

"use client";

import { useState } from "react";
import { createMarketMaker, killStrategy } from "@/lib/api";
import type { StrategyMetricsMessage } from "@/lib/types";
import clsx from "clsx";

interface Props {
  symbol: string;
  metrics: StrategyMetricsMessage | null;
}

export default function StrategyPanel({ symbol, metrics }: Props) {
  const [stratId, setStratId] = useState("mm_v1");
  const [spreadTicks, setSpreadTicks] = useState("0.05");
  const [quoteSize, setQuoteSize] = useState("100");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const strategies = metrics?.strategies ?? {};
  const activeCount = Object.keys(strategies).length;

  const handleLaunch = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setSuccess(null);
    setLoading(true);
    try {
      const res = await createMarketMaker({
        strategy_id: stratId,
        symbol: symbol.toUpperCase(),
        spread_ticks: parseFloat(spreadTicks),
        quote_size: parseInt(quoteSize, 10),
        inventory_skew_ticks: 0.1,
        max_position: 500,
        max_order_notional: 1_000_000,
      });
      setSuccess(`${res.strategy_id}: ${res.status}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Launch failed");
    } finally {
      setLoading(false);
    }
  };

  const handleKill = async (sid: string) => {
    try {
      await killStrategy(sid);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Kill failed");
    }
  };

  return (
    <div className="flex flex-col gap-3">
      {/* Active strategies */}
      {activeCount === 0 ? (
        <div className="text-xs text-terminal-muted text-center py-4 border border-dashed border-terminal-border rounded">
          No strategies running
        </div>
      ) : (
        <div className="space-y-2">
          {Object.entries(strategies).map(([sid, s]) => (
            <StrategyCard key={sid} stratId={sid} stats={s} onKill={handleKill} />
          ))}
        </div>
      )}

      {/* Launch form */}
      <form onSubmit={handleLaunch} className="flex flex-col gap-2 pt-2 border-t border-terminal-border">
        <div className="text-xs text-terminal-muted font-semibold">Launch Market Maker</div>

        <label className="flex flex-col gap-0.5">
          <span className="text-[10px] text-terminal-muted">Strategy ID</span>
          <input
            value={stratId}
            onChange={(e) => setStratId(e.target.value)}
            className="bg-terminal-bg border border-terminal-border rounded px-2 py-1 text-xs text-terminal-text focus:border-terminal-accent outline-none"
          />
        </label>

        <div className="grid grid-cols-2 gap-2">
          <label className="flex flex-col gap-0.5">
            <span className="text-[10px] text-terminal-muted">Spread (ticks)</span>
            <input
              type="number"
              step="0.01"
              value={spreadTicks}
              onChange={(e) => setSpreadTicks(e.target.value)}
              className="bg-terminal-bg border border-terminal-border rounded px-2 py-1 text-xs text-terminal-text focus:border-terminal-accent outline-none"
            />
          </label>
          <label className="flex flex-col gap-0.5">
            <span className="text-[10px] text-terminal-muted">Quote size</span>
            <input
              type="number"
              value={quoteSize}
              onChange={(e) => setQuoteSize(e.target.value)}
              className="bg-terminal-bg border border-terminal-border rounded px-2 py-1 text-xs text-terminal-text focus:border-terminal-accent outline-none"
            />
          </label>
        </div>

        <button
          type="submit"
          disabled={loading}
          className={clsx(
            "py-1.5 bg-terminal-accent/20 border border-terminal-accent/50 text-terminal-accent",
            "text-xs rounded hover:bg-terminal-accent/30 transition-colors",
            loading && "opacity-50 cursor-not-allowed"
          )}
        >
          {loading ? "Launching…" : "▶ Launch"}
        </button>
      </form>

      {error && <div className="text-xs text-ask">{error}</div>}
      {success && <div className="text-xs text-bid">{success}</div>}
    </div>
  );
}

function StrategyCard({
  stratId,
  stats,
  onKill,
}: {
  stratId: string;
  stats: Record<string, number | string | null>;
  onKill: (id: string) => void;
}) {
  const pnl = typeof stats.unrealised_pnl === "number" ? stats.unrealised_pnl : null;
  const pos = typeof stats.position === "number" ? stats.position : null;
  const fills = typeof stats.fill_count === "number" ? stats.fill_count : null;

  return (
    <div className="bg-terminal-bg border border-terminal-border rounded p-2 text-xs">
      <div className="flex justify-between items-center mb-1">
        <span className="font-semibold text-terminal-accent">{stratId}</span>
        <button
          onClick={() => onKill(stratId)}
          className="text-ask text-[10px] hover:bg-ask/10 px-1.5 py-0.5 rounded border border-ask/40 transition-colors"
        >
          KILL
        </button>
      </div>
      <div className="grid grid-cols-3 gap-1 text-[11px]">
        <Stat label="PnL" value={pnl != null ? `$${pnl.toFixed(2)}` : "—"} color={pnl != null ? (pnl >= 0 ? "text-bid" : "text-ask") : "text-terminal-muted"} />
        <Stat label="Position" value={pos != null ? String(pos) : "—"} />
        <Stat label="Fills" value={fills != null ? String(fills) : "—"} />
      </div>
    </div>
  );
}

function Stat({ label, value, color = "text-terminal-text" }: { label: string; value: string; color?: string }) {
  return (
    <div className="flex flex-col items-center">
      <span className="text-terminal-muted text-[9px]">{label}</span>
      <span className={clsx("font-semibold", color)}>{value}</span>
    </div>
  );
}
