import { useState } from "react";
import type { Platform, ValueOpportunity } from "../../types/arbitrage";
import { ValueCard } from "./ValueCard";
import { formatPlatform } from "../../utils/formatters";

interface Props {
  valueOps:         ValueOpportunity[];
  allPlatforms:     Platform[];
  enabledPlatforms: Platform[];
  onTogglePlatform: (plat: Platform) => void;
}

function ValueExplainer() {
  const [open, setOpen] = useState(false);

  return (
    <div className="rounded-xl border border-blue-800/40 bg-blue-950/20 overflow-hidden">
      {/* Toggle header */}
      <button
        onClick={() => setOpen(v => !v)}
        className="w-full flex items-center justify-between px-4 py-3 text-left
                   hover:bg-blue-900/10 transition-colors"
      >
        <div className="flex items-center gap-2">
          <span className="text-blue-400 text-sm font-semibold">What is a Value signal?</span>
          <span className="px-1.5 py-0.5 rounded text-xs bg-blue-900/40 text-blue-300 border border-blue-800/40">
            Value
          </span>
        </div>
        <svg
          className={`w-4 h-4 text-blue-600 transition-transform ${open ? "rotate-180" : ""}`}
          viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
          strokeLinecap="round" strokeLinejoin="round"
        >
          <path d="M6 9l6 6 6-6" />
        </svg>
      </button>

      {/* Explanation body */}
      {open && (
        <div className="px-4 pb-4 space-y-3 text-xs text-gray-300 border-t border-blue-800/30">
          <div className="pt-3 space-y-2">
            <p>
              A <span className="text-blue-300 font-semibold">Value signal</span> is a{" "}
              <span className="text-white">single-leg bet</span> — you place one bet on a sportsbook,
              with no hedge. It appears when the{" "}
              <span className="text-emerald-400">Kalshi prediction market</span> assigns a meaningfully
              higher probability to an outcome than a sportsbook's implied odds do.
            </p>
            <p>
              Since Kalshi trades as a true prediction market (no built-in vig), its prices are
              considered a <span className="text-white">sharper probability estimate</span> than
              sportsbook lines. When the sportsbook underprices an outcome relative to Kalshi,
              that sportsbook bet has positive cross-market EV.
            </p>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 pt-1">
            <div className="bg-gray-900/60 rounded-lg p-3 border border-gray-800">
              <p className="text-emerald-400 font-semibold mb-1">Oracle (Kalshi / Pinnacle)</p>
              <p className="text-gray-400">
                The reference probability — the "true" likelihood of the outcome according to
                the sharpest available market. Signals use the Pinnacle Consensus line
                (live market average) as the oracle.
              </p>
            </div>
            <div className="bg-gray-900/60 rounded-lg p-3 border border-gray-800">
              <p className="text-blue-400 font-semibold mb-1">Edge (pp)</p>
              <p className="text-gray-400">
                Percentage-point gap between the oracle probability and the sportsbook's
                implied probability. Only signals with ≥ 3 pp edge are shown.
                Larger edge = stronger signal.
              </p>
            </div>
            <div className="bg-gray-900/60 rounded-lg p-3 border border-gray-800">
              <p className="text-red-400 font-semibold mb-1">Risk profile</p>
              <p className="text-gray-400">
                <span className="text-white">Max loss = full stake</span> if the outcome doesn't occur.
                This is a directional bet, not a hedge. Only place value bets where you accept
                the binary risk.
              </p>
            </div>
          </div>

          <div className="bg-gray-900/60 rounded-lg p-3 border border-gray-800 space-y-1">
            <p className="text-gray-400 font-semibold text-xs">Example</p>
            <p className="text-gray-400">
              Kalshi prices "Lakers to win" at <span className="text-emerald-400">65%</span> (oracle).
              DraftKings only implies <span className="text-white">57%</span> (+75 American odds).
              The 8 pp gap means DraftKings is underpricing this outcome — betting
              Lakers on DK has positive cross-market EV relative to Kalshi's sharper line.
              If this edge is real, over many similar bets you profit. But you can lose
              any individual bet.
            </p>
          </div>

          <div className="bg-yellow-900/20 rounded-lg p-3 border border-yellow-800/30">
            <p className="text-yellow-500/80 text-xs">
              <span className="font-semibold text-yellow-400">vs. EV+ Edge:</span>{" "}
              EV+ Edges hedge both sides and limit max loss to ~10%. Value signals are
              unhedged single bets with unlimited downside up to your full stake.
              Value signals typically appear more frequently but carry higher individual risk.
            </p>
          </div>
        </div>
      )}
    </div>
  );
}

export function ValueList({ valueOps, allPlatforms, enabledPlatforms, onTogglePlatform }: Props) {
  const enabledFiltered = valueOps.filter(
    v => enabledPlatforms.includes(v.sb_leg.contract.platform as Platform)
  );
  const hiddenPlatforms = allPlatforms.filter(p => !enabledPlatforms.includes(p));

  // Platform toggles — always rendered; state lives in Dashboard so survives tab switches
  const platformToggles = (
    <div className="flex items-center gap-2 flex-wrap">
      <span className="text-xs text-gray-500">Show:</span>
      {allPlatforms.map(plat => {
        const on     = enabledPlatforms.includes(plat);
        const count  = valueOps.filter(v => v.sb_leg.contract.platform === plat).length;
        const isLast = enabledPlatforms.length === 1 && on;
        return (
          <button
            key={plat}
            onClick={() => onTogglePlatform(plat)}
            disabled={isLast}
            title={isLast ? "At least one platform must be visible" : undefined}
            className={`flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-medium border transition-colors ${
              on
                ? "bg-blue-900/60 border-blue-600 text-blue-200 hover:bg-blue-900/40"
                : "bg-gray-900/40 border-gray-700/50 text-gray-600 line-through hover:border-gray-600 hover:text-gray-500"
            } ${isLast ? "cursor-not-allowed opacity-60" : "cursor-pointer"}`}
          >
            <svg
              className={`w-3 h-3 flex-shrink-0 ${on ? "text-blue-400" : "text-gray-600"}`}
              viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="2"
              strokeLinecap="round" strokeLinejoin="round"
            >
              {on
                ? <path d="M2 6l3 3 5-5" />
                : <><path d="M2 2l8 8" /><path d="M10 2l-8 8" /></>
              }
            </svg>
            {formatPlatform(plat)}
            <span className={`${on ? "text-blue-300" : "text-gray-700"}`}>{count}</span>
          </button>
        );
      })}
    </div>
  );

  if (valueOps.length === 0) {
    return (
      <div className="space-y-4">
        <ValueExplainer />
        {platformToggles}
        <div className="text-center py-12 text-gray-600">
          <p className="font-medium text-gray-500">No value signals found</p>
          <p className="text-xs mt-1 max-w-sm mx-auto text-gray-600">
            Signals appear when the oracle probability for an outcome is
            meaningfully higher than the prediction market's implied probability.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <ValueExplainer />
      {platformToggles}
      <p className="text-xs text-gray-600 pt-0.5">
        {enabledFiltered.length} signal{enabledFiltered.length !== 1 ? "s" : ""}
        {hiddenPlatforms.length > 0 && ` · ${hiddenPlatforms.map(formatPlatform).join(", ")} hidden`}
        {" "}· oracle probability exceeds prediction-market implied probability by ≥ 3 pp · click to expand
      </p>
      {enabledFiltered.map(v => (
        <ValueCard key={v.id} value={v} />
      ))}
    </div>
  );
}
