import { useState } from "react";
import type { NearCertaintyMarket } from "../../types/arbitrage";

// ── Helpers ──────────────────────────────────────────────────────────────────

const PLATFORM_LABELS: Record<string, string> = {
  kalshi:      "Kalshi",
  polymarket:  "Polymarket",
  predictit:   "PredictIt",
  draftkings:  "DraftKings",
  fanduel:     "FanDuel",
  betmgm:      "BetMGM",
  caesars:     "Caesars",
  pinnacle:    "Pinnacle",
  odds_api:    "Odds API",
};

function formatPlatform(p: string) {
  return PLATFORM_LABELS[p] ?? p;
}

function formatClose(iso: string | null): string {
  if (!iso) return "—";
  const d   = new Date(iso);
  const now = new Date();
  const diffMs = d.getTime() - now.getTime();
  const diffH  = diffMs / (1000 * 60 * 60);
  if (diffH < 0)  return "Expired";
  if (diffH < 1)  return `${Math.round(diffH * 60)}m`;
  if (diffH < 24) return `${Math.round(diffH)}h`;
  return `${Math.floor(diffH / 24)}d`;
}

function tierColour(prob: number) {
  if (prob >= 99) return {
    bar:    "bg-purple-400",
    text:   "text-purple-300",
    border: "border-purple-700/40",
    bg:     "bg-purple-900/10",
  };
  if (prob >= 98) return {
    bar:    "bg-purple-500",
    text:   "text-purple-400",
    border: "border-purple-700/40",
    bg:     "bg-purple-900/10",
  };
  return {
    bar:    "bg-violet-600",
    text:   "text-violet-400",
    border: "border-violet-700/40",
    bg:     "bg-violet-900/10",
  };
}

// ── Platform filter ───────────────────────────────────────────────────────────

const ALL_NC_PLATFORMS = ["kalshi", "polymarket"] as const;
type NcPlatform = (typeof ALL_NC_PLATFORMS)[number];

// ── Card ─────────────────────────────────────────────────────────────────────

function NearCertaintyCard({ market }: { market: NearCertaintyMarket }) {
  const col      = tierColour(market.implied_prob);
  const pct      = Math.min(market.implied_prob, 100);
  const timeLeft = formatClose(market.close_time as string | null);
  const expired  = timeLeft === "Expired";

  return (
    <div className={`rounded-lg border ${col.border} ${col.bg} p-3 space-y-2`}>

      {/* Platform + closes + probability */}
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-1.5 min-w-0 text-xs">
          <span className="text-gray-500 shrink-0">{formatPlatform(market.platform)}</span>
          {!expired ? (
            <span className="text-gray-600 shrink-0">· closes {timeLeft}</span>
          ) : (
            <span className="text-red-500/70 shrink-0">· expired</span>
          )}
        </div>
        <span className={`text-lg font-bold tabular-nums shrink-0 leading-none ${col.text}`}>
          {market.implied_prob.toFixed(1)}¢
        </span>
      </div>

      {/* Event title */}
      <p className="text-white text-xs font-medium leading-snug line-clamp-2">
        {market.event_title}
      </p>

      {/* Outcome label */}
      <p className={`text-xs font-semibold ${col.text}`}>
        {market.outcome_label}
      </p>

      {/* Probability bar — almost full */}
      <div className="h-1 rounded-full bg-gray-800 overflow-hidden">
        <div
          className={`h-full rounded-full ${col.bar}`}
          style={{ width: `${pct}%` }}
        />
      </div>

      {/* Footer */}
      <div className="flex items-center justify-between gap-2 pt-0.5">
        {market.volume_24h != null && market.volume_24h > 0 ? (
          <span className="text-xs text-gray-600">
            Vol: ${market.volume_24h >= 1_000
              ? `${(market.volume_24h / 1_000).toFixed(1)}k`
              : market.volume_24h.toFixed(0)}
          </span>
        ) : <span />}
        {market.url && (
          <a
            href={market.url}
            target="_blank"
            rel="noreferrer"
            className={`text-xs underline hover:opacity-80 ${col.text}`}
          >
            Open →
          </a>
        )}
      </div>
    </div>
  );
}

// ── Threshold filter ─────────────────────────────────────────────────────────

const THRESHOLDS = [97, 98, 99] as const;
type Threshold   = (typeof THRESHOLDS)[number];

function ThresholdBar({
  threshold,
  onThreshold,
  total,
}: {
  threshold:   Threshold;
  onThreshold: (t: Threshold) => void;
  total:       number;
}) {
  return (
    <div className="flex items-center justify-between gap-3 flex-wrap">
      <p className="text-xs text-gray-500">
        {total} market{total !== 1 ? "s" : ""} at ≥{threshold}¢
      </p>
      <div className="flex gap-1">
        {THRESHOLDS.map(t => (
          <button
            key={t}
            onClick={() => onThreshold(t)}
            className={`px-2.5 py-1 rounded text-xs font-medium transition-colors ${
              threshold === t
                ? "bg-purple-900/60 text-purple-300 border border-purple-600"
                : "bg-gray-800 text-gray-400 border border-gray-700 hover:border-gray-500 hover:text-gray-300"
            }`}
          >
            ≥{t}¢
          </button>
        ))}
      </div>
    </div>
  );
}

// ── Sort bar ──────────────────────────────────────────────────────────────────

type SortKey = "price" | "close_time";

const SORT_OPTIONS: { key: SortKey; label: string }[] = [
  { key: "price",      label: "Probability ↓" },
  { key: "close_time", label: "Closes soonest" },
];

function SortBar({ sortBy, onSort }: { sortBy: SortKey; onSort: (s: SortKey) => void }) {
  return (
    <div className="flex items-center gap-2 flex-wrap">
      <span className="text-xs text-gray-500">Sort:</span>
      <div className="flex gap-1">
        {SORT_OPTIONS.map(opt => (
          <button
            key={opt.key}
            onClick={() => onSort(opt.key)}
            className={`px-2.5 py-1 rounded text-xs font-medium transition-colors ${
              sortBy === opt.key
                ? "bg-purple-900/60 text-purple-300 border border-purple-600"
                : "bg-gray-800 text-gray-400 border border-gray-700 hover:border-gray-500 hover:text-gray-300"
            }`}
          >
            {opt.label}
          </button>
        ))}
      </div>
    </div>
  );
}

// ── Main tab component ────────────────────────────────────────────────────────

/** Returns true when a market should be hidden: priced at effectively 100¢ (no upside remaining). */
function isAt100c(m: NearCertaintyMarket): boolean {
  return m.implied_prob >= 99.95; // displays as 100.0¢ — guaranteed but unplayable
}

export function NearCertaintyTab({ markets }: { markets: NearCertaintyMarket[] }) {
  const [threshold, setThreshold]               = useState<Threshold>(97);
  const [enabledPlatforms, setEnabledPlatforms] = useState<NcPlatform[]>([...ALL_NC_PLATFORMS]);
  const [sortBy, setSortBy]                     = useState<SortKey>("price");

  const togglePlatform = (plat: NcPlatform) => {
    setEnabledPlatforms(prev => {
      if (prev.includes(plat)) {
        if (prev.length === 1) return prev; // keep at least one
        return prev.filter(p => p !== plat);
      }
      return [...prev, plat];
    });
  };

  // Platform toggles — matching the ValueList pill pattern, in purple
  const platformToggles = (
    <div className="flex items-center gap-2 flex-wrap">
      <span className="text-xs text-gray-500">Show:</span>
      {ALL_NC_PLATFORMS.map(plat => {
        const on    = enabledPlatforms.includes(plat);
        const count = markets
          .filter(m => m.implied_prob >= threshold && !isAt100c(m))
          .filter(m => m.platform === plat).length;
        const isLast = enabledPlatforms.length === 1 && on;
        return (
          <button
            key={plat}
            onClick={() => togglePlatform(plat)}
            disabled={isLast}
            title={isLast ? "At least one platform must be visible" : undefined}
            className={`flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-medium border transition-colors ${
              on
                ? "bg-purple-900/60 border-purple-600 text-purple-200 hover:bg-purple-900/40"
                : "bg-gray-900/40 border-gray-700/50 text-gray-600 line-through hover:border-gray-600 hover:text-gray-500"
            } ${isLast ? "cursor-not-allowed opacity-60" : "cursor-pointer"}`}
          >
            <svg
              className={`w-3 h-3 flex-shrink-0 ${on ? "text-purple-400" : "text-gray-600"}`}
              viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="2"
              strokeLinecap="round" strokeLinejoin="round"
            >
              {on
                ? <path d="M2 6l3 3 5-5" />
                : <><path d="M2 2l8 8" /><path d="M10 2l-8 8" /></>
              }
            </svg>
            {formatPlatform(plat)}
            <span className={`${on ? "text-purple-300" : "text-gray-700"}`}>{count}</span>
          </button>
        );
      })}
    </div>
  );

  const filtered = markets
    .filter(m => m.implied_prob >= threshold)
    .filter(m => !isAt100c(m))
    .filter(m => enabledPlatforms.includes(m.platform as NcPlatform));

  const sorted = sortBy === "close_time"
    ? [...filtered].sort((a, b) => {
        // Null close times sink to the bottom
        if (!a.close_time && !b.close_time) return 0;
        if (!a.close_time) return 1;
        if (!b.close_time) return -1;
        return (
          new Date(a.close_time as string).getTime() -
          new Date(b.close_time as string).getTime()
        );
      })
    : [...filtered].sort((a, b) => b.price - a.price);

  if (markets.length === 0) {
    return (
      <div className="space-y-4">
        {platformToggles}
        <div className="text-center py-16 text-gray-600 text-sm">
          No near-certainty markets found yet — trigger a scan to refresh.
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <ThresholdBar
        threshold={threshold}
        onThreshold={setThreshold}
        total={sorted.length}
      />

      <div className="flex flex-wrap gap-y-2 items-center justify-between">
        {platformToggles}
        <SortBar sortBy={sortBy} onSort={setSortBy} />
      </div>

      {sorted.length === 0 ? (
        <div className="text-center py-12 text-gray-600 text-sm">
          No markets at ≥{threshold}¢ right now.
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {sorted.map(m => (
            <NearCertaintyCard key={m.id} market={m} />
          ))}
        </div>
      )}
    </div>
  );
}
