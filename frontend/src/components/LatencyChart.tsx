/**
 * Latency Monitor
 *
 * Rolling 2-minute chart of p50 / p99 / p999 processing latency.
 * The p999 line shows GC pauses and OS jitter — the numbers that
 * actually matter in HFT infrastructure.
 *
 * Gauge row shows current snapshot values.
 */

"use client";

import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from "recharts";
import type { LatencyPoint, LatencySnapshotMessage } from "@/lib/types";

interface Props {
  history: LatencyPoint[];
  current: LatencySnapshotMessage | null;
}

function fmt(v: number | null | undefined, unit = "µs") {
  if (v == null) return "—";
  if (v >= 1000) return `${(v / 1000).toFixed(1)} ms`;
  return `${v.toFixed(1)} ${unit}`;
}

export default function LatencyChart({ history, current }: Props) {
  const chartData = history.map((p) => ({
    t: new Date(p.t).toLocaleTimeString("en-US", { hour12: false }),
    p50:  p.p50,
    p99:  p.p99,
    p999: p.p999,
  }));

  return (
    <div className="flex flex-col gap-4 h-full">
      {/* Gauge row */}
      <div className="grid grid-cols-3 gap-2">
        <Gauge label="p50" value={current?.p50_us} color="#3fb950" />
        <Gauge label="p99" value={current?.p99_us} color="#d29922" />
        <Gauge label="p999" value={current?.p999_us} color="#f85149" />
      </div>

      {/* Counters */}
      <div className="grid grid-cols-3 gap-2 text-xs text-terminal-muted">
        <span>Orders: <span className="text-terminal-text">{current?.orders_received?.toLocaleString() ?? "—"}</span></span>
        <span>Trades: <span className="text-terminal-text">{current?.trades_executed?.toLocaleString() ?? "—"}</span></span>
        <span>Drops: <span className={current?.dropped_events ? "text-ask" : "text-terminal-text"}>{current?.dropped_events ?? 0}</span></span>
      </div>

      {/* Rolling chart */}
      <div className="flex-1 min-h-0">
        {chartData.length < 2 ? (
          <div className="flex items-center justify-center h-full text-terminal-muted text-xs">
            Collecting samples…
          </div>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={chartData} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#21262d" />
              <XAxis
                dataKey="t"
                tick={{ fill: "#8b949e", fontSize: 10 }}
                tickLine={false}
                interval="preserveStartEnd"
              />
              <YAxis
                tick={{ fill: "#8b949e", fontSize: 10 }}
                tickLine={false}
                axisLine={false}
                tickFormatter={(v: number) => v >= 1000 ? `${(v / 1000).toFixed(0)}ms` : `${v}µs`}
              />
              <Tooltip
                contentStyle={{ backgroundColor: "#161b22", border: "1px solid #21262d", borderRadius: 4 }}
                labelStyle={{ color: "#8b949e" }}
                formatter={(value: number) => [fmt(value), ""]}
              />
              <Legend wrapperStyle={{ fontSize: 10, color: "#8b949e" }} />
              <Line type="monotone" dataKey="p50"  stroke="#3fb950" dot={false} strokeWidth={1.5} name="p50" />
              <Line type="monotone" dataKey="p99"  stroke="#d29922" dot={false} strokeWidth={1.5} name="p99" />
              <Line type="monotone" dataKey="p999" stroke="#f85149" dot={false} strokeWidth={1.5} name="p999" />
            </LineChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  );
}

function Gauge({ label, value, color }: { label: string; value: number | null | undefined; color: string }) {
  return (
    <div className="bg-terminal-bg border border-terminal-border rounded p-2 text-center">
      <div className="text-[10px] text-terminal-muted mb-0.5">{label}</div>
      <div className="text-lg font-semibold" style={{ color }}>
        {fmt(value)}
      </div>
    </div>
  );
}
