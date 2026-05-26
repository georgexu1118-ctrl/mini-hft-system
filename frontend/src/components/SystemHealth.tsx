/**
 * System health bar — shown in the top header.
 * Green OK badge or amber DEGRADED with drop counters.
 */

"use client";

import type { SystemHealthMessage } from "@/lib/types";
import clsx from "clsx";

interface Props {
  health: SystemHealthMessage | null;
  connected: boolean;
  lastHeartbeat: number | null;
}

export default function SystemHealth({ health, connected, lastHeartbeat }: Props) {
  const stale = lastHeartbeat ? Date.now() - lastHeartbeat > 60_000 : true;
  const degraded = !connected || stale || health?.status === "DEGRADED";

  return (
    <div className="flex items-center gap-3 text-xs">
      {/* WS connection dot */}
      <span className="flex items-center gap-1">
        <span
          className={clsx(
            "inline-block w-2 h-2 rounded-full",
            connected ? "bg-bid animate-pulse-slow" : "bg-ask"
          )}
        />
        <span className="text-terminal-muted">{connected ? "Live" : "Reconnecting…"}</span>
      </span>

      {/* Engine status badge */}
      {health ? (
        <span
          className={clsx(
            "px-2 py-0.5 rounded text-[10px] font-semibold border",
            degraded
              ? "bg-trade/10 text-trade border-trade/40"
              : "bg-bid/10 text-bid border-bid/40"
          )}
        >
          {health.status}
        </span>
      ) : null}

      {/* Drop counters (only show if non-zero) */}
      {health && health.event_bus_drops > 0 && (
        <span className="text-ask text-[10px]">
          bus_drops={health.event_bus_drops}
        </span>
      )}
      {health && health.persistence_drops > 0 && (
        <span className="text-ask text-[10px]">
          persist_drops={health.persistence_drops}
        </span>
      )}
    </div>
  );
}
