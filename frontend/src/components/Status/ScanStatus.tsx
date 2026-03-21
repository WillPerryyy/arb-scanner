import { useState } from "react";
import type { ScannerStatus } from "../../types/arbitrage";
import { PLATFORM_LABELS } from "../../utils/formatters";
import { timeSince } from "../../utils/formatters";

interface Props {
  statuses:     ScannerStatus[];
  isConnected:  boolean;
  lastUpdated:  Date | null;
  totalMarkets: number;
}

export function ScanStatus({ statuses, isConnected, lastUpdated, totalMarkets }: Props) {
  const [showDetail, setShowDetail] = useState(false);

  const healthyCount  = statuses.filter(s => s.is_healthy).length;
  const totalCount    = statuses.length;
  const allHealthy    = healthyCount === totalCount && totalCount > 0;
  const someUnhealthy = healthyCount < totalCount && totalCount > 0;

  return (
    <div className="space-y-2">
      {/* ── Compact summary bar ── */}
      <div className="flex items-center gap-3 text-xs text-gray-400">
        {/* Live / reconnecting dot */}
        <div className="flex items-center gap-1.5">
          <span
            className={`w-2 h-2 rounded-full shrink-0 ${
              isConnected ? "bg-green-400 animate-pulse" : "bg-red-400"
            }`}
          />
          <span className={isConnected ? "text-green-400 font-medium" : "text-red-400"}>
            {isConnected ? "Live" : "Reconnecting…"}
          </span>
        </div>

        <span className="text-gray-700">·</span>

        {/* Platform health summary */}
        {totalCount > 0 && (
          <span className={someUnhealthy ? "text-yellow-400" : "text-gray-400"}>
            {healthyCount}/{totalCount} platforms
            {allHealthy ? "" : someUnhealthy ? " ⚠" : ""}
          </span>
        )}

        <span className="text-gray-700">·</span>

        {/* Market count */}
        <span>{totalMarkets.toLocaleString()} markets</span>

        {/* Last updated */}
        {lastUpdated && (
          <>
            <span className="text-gray-700">·</span>
            <span className="text-gray-500">updated {timeSince(lastUpdated.toISOString())}</span>
          </>
        )}

        {/* Toggle detail */}
        {totalCount > 0 && (
          <button
            onClick={() => setShowDetail(v => !v)}
            className="ml-auto text-gray-600 hover:text-gray-400 transition-colors text-xs underline decoration-dotted"
          >
            {showDetail ? "hide detail" : "show detail"}
          </button>
        )}
      </div>

      {/* ── Per-scanner detail (collapsible) ── */}
      {showDetail && (
        <div className="flex flex-wrap gap-1.5 pt-1">
          {statuses.map(s => (
            <div
              key={s.platform}
              title={s.error ?? undefined}
              className={`
                flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium cursor-default
                ${s.is_healthy
                  ? "bg-green-900/40 text-green-300 border border-green-800/60"
                  : "bg-red-900/40 text-red-300 border border-red-800/60"
                }
              `}
            >
              <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${s.is_healthy ? "bg-green-400" : "bg-red-400"}`} />
              {PLATFORM_LABELS[s.platform] ?? s.platform}
              <span className="opacity-50">·</span>
              <span className="opacity-70">{s.markets_found.toLocaleString()}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
