import { useState } from "react";
import type { ValueOpportunity } from "../../types/arbitrage";
import { formatPlatform, timeSince } from "../../utils/formatters";

function formatOddsFromDecimal(d: number): string {
  if (d >= 2) return `+${Math.round((d - 1) * 100)}`;
  return `-${Math.round(100 / (d - 1))}`;
}

interface Props {
  value: ValueOpportunity;
}

export function ValueCard({ value }: Props) {
  const [expanded, setExpanded] = useState(false);
  const [stakeInput, setStakeInput] = useState<string>("");

  // Is this a prediction-market (Kalshi) bet rather than a sportsbook bet?
  const isPM = value.sb_leg.contract.platform === "kalshi";

  const refStake   = value.sb_leg.stake;          // $10 reference
  const parsedStake = parseFloat(stakeInput);
  const scale      = stakeInput && !isNaN(parsedStake) && parsedStake > 0
    ? parsedStake / refStake
    : 1;

  const scaledStake  = refStake * scale;
  const scaledPayout = value.sb_leg.expected_payout * scale;
  const scaledProfit = scaledPayout - scaledStake;

  // For SB: use decimal_odds. For Kalshi: derive from price (D = 1/price).
  const dec = isPM
    ? 1.0 / value.sb_leg.contract.price
    : (value.sb_leg.contract.decimal_odds ?? 1);
  const americanOdds = isPM ? null : formatOddsFromDecimal(dec);

  // Visual probability bar widths (capped at 100 for display)
  const oracleBarW = Math.round(Math.min(value.oracle_prob     * 100, 100));
  const sbBarW     = Math.round(Math.min(value.sb_implied_prob * 100, 100));

  const outcomeLabel = value.sb_leg.contract.outcome_label || "Outcome";

  return (
    <div
      onClick={() => setExpanded(e => !e)}
      className="bg-gray-900 border border-gray-800 rounded-xl p-4 cursor-pointer
                 hover:border-blue-700/60 transition-colors select-none"
    >
      {/* Header row */}
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <p className="font-semibold text-white text-sm leading-snug">
            {value.event_title}
          </p>
          <div className="flex items-center gap-1.5 mt-1.5 flex-wrap text-xs">
            <span className="text-gray-400">
              {formatPlatform(value.sb_leg.contract.platform)}{" "}
              <span className="text-blue-300 font-semibold">{outcomeLabel}</span>
            </span>
            <span className="text-gray-600">·</span>
            <span className="text-gray-500">oracle: {formatPlatform(value.oracle_platform)}</span>
            <span className="px-1.5 py-0.5 rounded text-xs font-medium bg-blue-900/40 text-blue-300 border border-blue-800/40">
              Value
            </span>
          </div>
        </div>

        {/* Primary metrics */}
        <div className="text-right shrink-0">
          <p className="font-bold text-base text-green-400">
            +{value.cross_ev_pct.toFixed(1)}% EV
          </p>
          <p className="text-xs mt-0.5 text-blue-400/80">
            +{value.edge_ppts.toFixed(1)} pp edge
          </p>
          <p className="text-xs mt-0.5 text-gray-500">
            {timeSince(value.detected_at)}
          </p>
        </div>
      </div>

      {/* Probability comparison bar */}
      <div className="mt-3 space-y-1.5">
        <div className="flex items-center gap-2 text-xs">
          <span className="w-16 text-right text-emerald-400 font-medium">
            {formatPlatform(value.oracle_platform)}
          </span>
          <div className="flex-1 bg-gray-800 rounded-full h-2 overflow-hidden">
            <div
              className="bg-emerald-500 h-2 rounded-full transition-all"
              style={{ width: `${oracleBarW}%` }}
            />
          </div>
          <span className="w-12 text-emerald-400 font-semibold">
            {(value.oracle_prob * 100).toFixed(1)}%
          </span>
        </div>
        <div className="flex items-center gap-2 text-xs">
          <span className="w-16 text-right text-gray-400">
            {formatPlatform(value.sb_leg.contract.platform)}
          </span>
          <div className="flex-1 bg-gray-800 rounded-full h-2 overflow-hidden">
            <div
              className="bg-gray-500 h-2 rounded-full transition-all"
              style={{ width: `${sbBarW}%` }}
            />
          </div>
          <span className="w-12 text-gray-400">
            {(value.sb_implied_prob * 100).toFixed(1)}%
          </span>
        </div>
      </div>

      {/* Expanded detail */}
      {expanded && (
        <div
          onClick={e => e.stopPropagation()}
          className="mt-4 border-t border-gray-800 pt-4 space-y-4 text-xs"
        >
          {/* Stake rescaler */}
          <div className="flex items-center gap-2 bg-gray-800/60 rounded-lg p-3">
            <span className="text-gray-300 text-xs font-medium">Stake $</span>
            <input
              type="number"
              min="0.01"
              step="1"
              value={stakeInput}
              onChange={e => setStakeInput(e.target.value)}
              onClick={e => e.stopPropagation()}
              placeholder={refStake.toFixed(2)}
              className="w-28 bg-gray-700 border border-gray-600 rounded px-2 py-1
                         text-white text-xs focus:outline-none focus:border-blue-500
                         placeholder-gray-500"
            />
            <span className="text-gray-500 text-xs">reference: ${refStake.toFixed(2)}</span>
            {scale !== 1 && (
              <button
                onClick={e => { e.stopPropagation(); setStakeInput(""); }}
                className="ml-auto text-gray-500 hover:text-white text-xs underline"
              >
                Reset
              </button>
            )}
          </div>

          {/* Bet details */}
          <div className="bg-gray-800/50 rounded-lg p-3 space-y-2">
            <p className="text-gray-400 font-semibold text-xs">Bet details</p>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <p className="text-gray-500">Platform</p>
                <p className="text-white font-medium">{formatPlatform(value.sb_leg.contract.platform)}</p>
              </div>
              <div>
                <p className="text-gray-500">Outcome</p>
                <p className="text-blue-300 font-medium">{outcomeLabel}</p>
              </div>
              <div>
                {isPM ? (
                  <>
                    <p className="text-gray-500">Price/share</p>
                    <p className="text-white">
                      ¢{(value.sb_leg.contract.price * 100).toFixed(2)}
                      <span className="text-gray-500 ml-1">({(1 / value.sb_leg.contract.price).toFixed(2)}x)</span>
                    </p>
                  </>
                ) : (
                  <>
                    <p className="text-gray-500">Odds</p>
                    <p className="text-white">{americanOdds} <span className="text-gray-500">({dec.toFixed(3)}x)</span></p>
                  </>
                )}
              </div>
              <div>
                <p className="text-gray-500">Stake</p>
                <p className="text-white font-medium">${scaledStake.toFixed(2)}</p>
              </div>
              <div>
                <p className="text-gray-500">Win payout</p>
                <p className="text-green-400 font-medium">${scaledPayout.toFixed(2)}</p>
              </div>
              <div>
                <p className="text-gray-500">Net profit if win</p>
                <p className="text-green-400 font-medium">+${scaledProfit.toFixed(2)}</p>
              </div>
            </div>
          </div>

          {/* Probability breakdown */}
          <div className="bg-gray-800/50 rounded-lg p-3 space-y-2">
            <p className="text-gray-400 font-semibold text-xs">Probability analysis</p>
            <div className="grid grid-cols-3 gap-3">
              <div>
                <p className="text-gray-500">{formatPlatform(value.oracle_platform)} oracle</p>
                <p className="text-emerald-400 font-semibold">{(value.oracle_prob * 100).toFixed(2)}%</p>
              </div>
              <div>
                <p className="text-gray-500">{isPM ? "Kalshi price" : "SB implied"}</p>
                <p className="text-gray-300">{(value.sb_implied_prob * 100).toFixed(2)}%</p>
              </div>
              <div>
                <p className="text-gray-500">Edge</p>
                <p className="text-blue-400 font-semibold">+{value.edge_ppts.toFixed(2)} pp</p>
              </div>
            </div>
            <div className="pt-2 border-t border-gray-700">
              <p className="text-gray-500">Cross-market EV at ${scaledStake.toFixed(2)} stake</p>
              <p className="text-green-400 font-semibold text-sm">
                +{value.cross_ev_pct.toFixed(2)}%
                <span className="text-xs font-normal text-gray-400 ml-2">
                  (≈ ${(scaledStake * value.cross_ev_pct / 100).toFixed(2)} expected edge)
                </span>
              </p>
            </div>
          </div>

          {/* Footer */}
          <div className="grid grid-cols-2 gap-2 pt-1 border-t border-gray-800">
            <div>
              <p className="text-gray-500">Match score</p>
              <p className="text-white">{(value.match_score * 100).toFixed(0)}%</p>
            </div>
            <div>
              <p className="text-gray-500">Detected</p>
              <p className="text-white">{timeSince(value.detected_at)}</p>
            </div>
          </div>

          {/* Link */}
          {value.sb_leg.contract.url && (
            <a
              href={value.sb_leg.contract.url}
              target="_blank"
              rel="noreferrer"
              onClick={e => e.stopPropagation()}
              className="inline-block text-blue-400 underline hover:opacity-80 text-xs"
            >
              Open {formatPlatform(value.sb_leg.contract.platform)} market →
            </a>
          )}

          <p className="text-yellow-600/70 text-xs bg-yellow-900/20 rounded p-2 border border-yellow-800/30">
            {formatPlatform(value.oracle_platform)} oracle = {(value.oracle_prob * 100).toFixed(1)}% is the
            sharp-line implied probability for this outcome — used as the reference.{" "}
            {isPM
              ? `Kalshi prices this at ¢${(value.sb_leg.contract.price * 100).toFixed(1)}/share (${(value.sb_implied_prob * 100).toFixed(1)}% implied), creating a`
              : `The sportsbook implies only ${(value.sb_implied_prob * 100).toFixed(1)}%, creating a`
            }{" "}
            +{value.cross_ev_pct.toFixed(1)}% cross-market EV. This is a single-leg bet signal;
            the max loss is your full stake if the outcome doesn&apos;t occur.
          </p>
        </div>
      )}
    </div>
  );
}
