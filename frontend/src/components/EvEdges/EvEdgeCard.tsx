import { useState, useMemo } from "react";
import type { EvEdgeOpportunity, ArbLeg, Platform } from "../../types/arbitrage";
import { formatPlatform, formatDollars, timeSince } from "../../utils/formatters";

const SPORTSBOOK_PLATFORMS: Set<Platform> = new Set([
  "draftkings", "fanduel", "betmgm", "caesars",
]);

function isLegSportsbook(leg: ArbLeg): boolean {
  return SPORTSBOOK_PLATFORMS.has(leg.contract.platform);
}

function getLegLabel(leg: ArbLeg): string {
  const label = leg.contract.outcome_label;
  if (label && label.toLowerCase() !== "yes" && label.toLowerCase() !== "no") {
    return label;
  }
  return label || "—";
}

function formatOdds(leg: ArbLeg): string {
  const d = leg.contract.decimal_odds;
  if (d == null) return "—";
  if (d >= 2) return `+${Math.round((d - 1) * 100)}`;
  return `-${Math.round(100 / (d - 1))}`;
}

function formatReturnPct(pct: number): string {
  const sign = pct >= 0 ? "+" : "";
  return `${sign}${pct.toFixed(1)}%`;
}

interface LegCardProps {
  leg:          ArbLeg;
  label:        string;
  theme:        "amber" | "violet";
  scaledStake:  number;
  scaledPayout: number;
  scaledTotal:  number;
}

function LegCard({ leg, label, theme, scaledStake, scaledPayout, scaledTotal }: LegCardProps) {
  const isSb = isLegSportsbook(leg);
  const border = theme === "amber"
    ? "bg-amber-900/20 border-amber-800/40"
    : "bg-violet-900/20 border-violet-800/40";
  const text = theme === "amber" ? "text-amber-400" : "text-violet-400";
  const linkColor = theme === "amber" ? "text-amber-400" : "text-violet-400";

  // P&L relative to total outlay (both legs), not just this leg's stake
  const profitOnWin = scaledPayout - scaledTotal;
  const profitSign = profitOnWin >= 0 ? "+" : "";

  return (
    <div className={`rounded-lg p-3 space-y-1.5 border text-xs ${border}`}>
      <p className={`font-semibold ${text}`}>
        {isSb ? "Sportsbook" : "Kalshi"} — BUY {label}
      </p>
      <p className="text-gray-400">
        Platform: <span className="text-white">{formatPlatform(leg.contract.platform)}</span>
      </p>
      {leg.contract.event_title && (
        <p className="text-gray-400 truncate">
          Contract:{" "}
          <span className="text-white text-xs">
            {leg.contract.event_title.slice(0, 55)}
            {leg.contract.event_title.length > 55 ? "…" : ""}
          </span>
        </p>
      )}
      <p className="text-gray-400">
        Stake: <span className="text-white font-medium">${scaledStake.toFixed(2)}</span>
      </p>
      {isSb && leg.contract.decimal_odds != null && (
        <p className="text-gray-400">
          Odds: <span className="text-white">{formatOdds(leg)}</span>
          <span className="text-gray-500 ml-1">({leg.contract.decimal_odds.toFixed(3)}x)</span>
        </p>
      )}
      {!isSb && (
        <p className="text-gray-400">
          Price/share:{" "}
          <span className="text-white">{formatDollars(leg.contract.price)}</span>
          <span className="text-gray-500 ml-1">
            ({(scaledStake / leg.contract.price).toFixed(1)} shares)
          </span>
        </p>
      )}
      <p className="text-gray-400">
        Return if this wins:{" "}
        <span className={profitOnWin >= 0 ? "text-green-400 font-medium" : "text-red-400 font-medium"}>
          ${scaledPayout.toFixed(2)}{" "}
          <span className="font-normal">
            ({profitSign}${Math.abs(profitOnWin).toFixed(2)})
          </span>
        </span>
      </p>
      {leg.contract.url && (
        <a
          href={leg.contract.url}
          target="_blank"
          rel="noreferrer"
          className={`inline-block mt-1 underline hover:opacity-80 ${linkColor}`}
        >
          Open market →
        </a>
      )}
    </div>
  );
}

interface Props {
  edge: EvEdgeOpportunity;
}

export function EvEdgeCard({ edge }: Props) {
  const [expanded, setExpanded] = useState(false);
  const [outlayInput, setOutlayInput] = useState<string>("");

  const scale = useMemo(() => {
    const parsed = parseFloat(outlayInput);
    if (!outlayInput || isNaN(parsed) || parsed <= 0) return 1;
    return parsed / edge.total_cost;
  }, [outlayInput, edge.total_cost]);

  const isScaled       = scale !== 1;
  const scaledSbStake  = edge.sb_stake * scale;
  const scaledPmStake  = edge.pm_stake * scale;
  const scaledTotal    = edge.total_cost * scale;
  const scaledSbPayout = edge.payout_if_sb_wins * scale;
  const scaledPmPayout = edge.payout_if_pm_wins * scale;
  const scaledAvgReturn = edge.avg_return * scale;

  // Probability-weighted P&L contributions (scale with outlay)
  const wtdNetA = edge.prob_leg_a * (scaledSbPayout - scaledTotal);
  const wtdNetB = edge.prob_leg_b * (scaledPmPayout - scaledTotal);

  const sbLabel  = getLegLabel(edge.sb_leg);
  const pmLabel  = getLegLabel(edge.pm_leg);
  // Determine if the pm_leg is a sportsbook (for SB+SB pairs)
  const pmIsSb   = isLegSportsbook(edge.pm_leg);

  // When Pinnacle oracle data is available, prob_leg_a + prob_leg_b = 1.0 exactly
  // (complementary probs from a single oracle). Without oracle they sum to > 1 (vig).
  const isOracleWeighted = (edge.prob_leg_a + edge.prob_leg_b) <= 1.001;

  // With Pinnacle oracle, weighted_ev_pct is a genuine probability-weighted EV
  // and will be positive for any displayed opportunity (filter applied in backend).
  // Without oracle, use avg_return_pct (50/50 average) as the sign indicator.
  const evColor = isOracleWeighted
    ? (edge.weighted_ev_pct >= 0 ? "text-green-400" : "text-red-400")
    : (edge.avg_return_pct  >= 0 ? "text-green-400" : "text-red-400");

  const sbPct  = Math.round(edge.sb_fraction * 100);
  const pmPct  = 100 - sbPct;

  return (
    <div
      onClick={() => setExpanded(e => !e)}
      className="bg-gray-900 border border-gray-800 rounded-xl p-4 cursor-pointer
                 hover:border-amber-700/60 transition-colors select-none"
    >
      {/* Header */}
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <p className="font-semibold text-white text-sm leading-snug">
            {edge.event_title}
          </p>
          <div className="flex items-center gap-1.5 mt-1.5 flex-wrap text-xs">
            <span className="text-gray-400">
              {formatPlatform(edge.sb_leg.contract.platform)}{" "}
              <span className="text-amber-400 font-semibold">{sbLabel}</span>
            </span>
            <span className="text-gray-600">+</span>
            <span className="text-gray-400">
              {formatPlatform(edge.pm_leg.contract.platform)}{" "}
              <span className="text-violet-400 font-semibold">{pmLabel}</span>
            </span>
            <span className="px-1.5 py-0.5 rounded text-xs font-medium bg-amber-900/40 text-amber-300 border border-amber-800/40">
              EV+
            </span>
            <span className="px-1.5 py-0.5 rounded text-xs font-medium bg-gray-800 text-gray-400 border border-gray-700">
              Δ {edge.delta_pct.toFixed(0)}%
            </span>
            <span className="px-1.5 py-0.5 rounded text-xs font-medium bg-gray-800 text-gray-500 border border-gray-700">
              {sbPct}% / {pmPct}%{pmIsSb ? " SB" : " Kal"}
            </span>
          </div>
        </div>

        {/* Primary metrics */}
        <div className="text-right shrink-0">
          {isOracleWeighted ? (
            <>
              <p className={`font-bold text-base ${evColor}`}>
                {formatReturnPct(edge.weighted_ev_pct)} EV
              </p>
              <p className="text-xs mt-0.5 text-emerald-500/80">
                Pinnacle-weighted
              </p>
            </>
          ) : (
            <>
              <p className={`font-bold text-base ${evColor}`}>
                {formatReturnPct(edge.avg_return_pct)} avg
              </p>
              <p className="text-xs mt-0.5 text-gray-500">
                wtd EV {formatReturnPct(edge.weighted_ev_pct)}
              </p>
            </>
          )}
          <p className="text-xs mt-0.5 text-red-400/80">
            max loss {edge.max_loss_pct.toFixed(1)}%
          </p>
          {isScaled && <p className="text-xs text-blue-400 mt-0.5">scaled</p>}
        </div>
      </div>

      {/* Key metrics row */}
      <div className="mt-3 grid grid-cols-4 gap-2 text-xs">
        <div>
          <p className="text-gray-500">Leg A stake</p>
          <p className="text-amber-400 font-medium">${scaledSbStake.toFixed(2)}</p>
        </div>
        <div>
          <p className="text-gray-500">Leg B stake</p>
          <p className="text-violet-400 font-medium">${scaledPmStake.toFixed(2)}</p>
        </div>
        <div>
          <p className="text-gray-500">If {sbLabel} wins</p>
          <p className={scaledSbPayout >= scaledTotal ? "text-green-400 font-medium" : "text-red-400 font-medium"}>
            ${scaledSbPayout.toFixed(2)}
          </p>
        </div>
        <div>
          <p className="text-gray-500">If {pmLabel} wins</p>
          <p className={scaledPmPayout >= scaledTotal ? "text-green-400 font-medium" : "text-red-400 font-medium"}>
            ${scaledPmPayout.toFixed(2)}
          </p>
        </div>
      </div>

      {/* Expanded detail */}
      {expanded && (
        <div
          onClick={e => e.stopPropagation()}
          className="mt-4 border-t border-gray-800 pt-4 space-y-3 text-xs"
        >
          {/* Outlay rescaler */}
          <div className="flex items-center gap-2 bg-gray-800/60 rounded-lg p-3">
            <span className="text-gray-300 text-xs font-medium">Total outlay $</span>
            <input
              type="number"
              min="0.01"
              step="1"
              value={outlayInput}
              onChange={e => setOutlayInput(e.target.value)}
              onClick={e => e.stopPropagation()}
              placeholder={edge.total_cost.toFixed(2)}
              className="w-28 bg-gray-700 border border-gray-600 rounded px-2 py-1
                         text-white text-xs focus:outline-none focus:border-amber-500
                         placeholder-gray-500"
            />
            <span className="text-gray-500 text-xs">
              base: ${edge.total_cost.toFixed(2)}{" "}
              (A: ${edge.sb_stake.toFixed(2)} / B: ${edge.pm_stake.toFixed(2)})
            </span>
            {isScaled && (
              <button
                onClick={e => { e.stopPropagation(); setOutlayInput(""); }}
                className="ml-auto text-gray-500 hover:text-white text-xs underline"
              >
                Reset
              </button>
            )}
          </div>

          {/* Two-leg breakdown */}
          <div className="grid grid-cols-2 gap-3">
            <LegCard
              leg={edge.sb_leg}
              label={sbLabel}
              theme="amber"
              scaledStake={scaledSbStake}
              scaledPayout={scaledSbPayout}
              scaledTotal={scaledTotal}
            />
            <LegCard
              leg={edge.pm_leg}
              label={pmLabel}
              theme="violet"
              scaledStake={scaledPmStake}
              scaledPayout={scaledPmPayout}
              scaledTotal={scaledTotal}
            />
          </div>

          {/* Outcome scenarios */}
          <div className="bg-gray-800/50 rounded-lg p-3 space-y-3">
            <p className="text-gray-400 font-semibold">Outcome scenarios</p>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <p className="text-amber-400 font-medium mb-1">
                  {sbLabel} wins ({formatPlatform(edge.sb_leg.contract.platform)} pays)
                </p>
                <p className="text-gray-300">
                  Return:{" "}
                  <span className={scaledSbPayout >= scaledTotal ? "text-green-400 font-semibold" : "text-red-400 font-semibold"}>
                    ${scaledSbPayout.toFixed(2)}
                  </span>
                </p>
                <p className="text-gray-500 mt-0.5">
                  P&L: {scaledSbPayout - scaledTotal >= 0 ? "+" : ""}${(scaledSbPayout - scaledTotal).toFixed(2)}
                </p>
                <p className="text-gray-600 mt-0.5">
                  {isOracleWeighted ? "Pinnacle P" : "P"}={`${(edge.prob_leg_a * 100).toFixed(1)}%`} · weighted:{" "}
                  <span className={wtdNetA >= 0 ? "text-green-500/70" : "text-red-500/70"}>
                    {wtdNetA >= 0 ? "+" : ""}${wtdNetA.toFixed(2)}
                  </span>
                </p>
              </div>
              <div>
                <p className="text-violet-400 font-medium mb-1">
                  {pmLabel} wins ({formatPlatform(edge.pm_leg.contract.platform)} pays)
                </p>
                <p className="text-gray-300">
                  Return:{" "}
                  <span className={scaledPmPayout >= scaledTotal ? "text-green-400 font-semibold" : "text-red-400 font-semibold"}>
                    ${scaledPmPayout.toFixed(2)}
                  </span>
                </p>
                <p className="text-gray-500 mt-0.5">
                  P&L: {scaledPmPayout - scaledTotal >= 0 ? "+" : ""}${(scaledPmPayout - scaledTotal).toFixed(2)}
                </p>
                <p className="text-gray-600 mt-0.5">
                  {isOracleWeighted ? "Pinnacle P" : "P"}={`${(edge.prob_leg_b * 100).toFixed(1)}%`} · weighted:{" "}
                  <span className={wtdNetB >= 0 ? "text-green-500/70" : "text-red-500/70"}>
                    {wtdNetB >= 0 ? "+" : ""}${wtdNetB.toFixed(2)}
                  </span>
                </p>
              </div>
            </div>

            {/* Probability breakdown + EV summary */}
            <div className="border-t border-gray-700 pt-3 space-y-2">
              <div className="grid grid-cols-3 gap-3 text-xs">
                <div>
                  <p className="text-gray-500">
                    {isOracleWeighted ? "Pinnacle prob (A)" : "Leg A implied prob"}
                  </p>
                  <p className="text-amber-400">{(edge.prob_leg_a * 100).toFixed(1)}%</p>
                </div>
                <div>
                  <p className="text-gray-500">
                    {isOracleWeighted ? "Pinnacle prob (B)" : "Leg B implied prob"}
                  </p>
                  <p className="text-violet-400">{(edge.prob_leg_b * 100).toFixed(1)}%</p>
                </div>
                <div>
                  {isOracleWeighted ? (
                    <>
                      <p className="text-gray-500">Oracle source</p>
                      <p className="text-blue-400">Pinnacle</p>
                    </>
                  ) : (
                    <>
                      <p className="text-gray-500">Combined vig</p>
                      <p className="text-gray-300">
                        +{((edge.prob_leg_a + edge.prob_leg_b - 1) * 100).toFixed(1)}%
                      </p>
                    </>
                  )}
                </div>
              </div>
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-gray-500">Prob-weighted EV</p>
                  <p className={`font-semibold text-sm ${evColor}`}>
                    {formatReturnPct(edge.weighted_ev_pct)}{" "}
                    <span className="text-xs font-normal text-gray-400">
                      (≈${(scaledTotal * edge.weighted_ev_pct / 100).toFixed(2)})
                    </span>
                  </p>
                </div>
                <div className="text-right">
                  <p className="text-gray-500">Avg return (50/50)</p>
                  <p className="text-gray-300 text-sm">
                    ${scaledAvgReturn.toFixed(2)}{" "}
                    <span className="text-xs text-gray-500">
                      ({formatReturnPct(edge.avg_return_pct)})
                    </span>
                  </p>
                </div>
              </div>
            </div>
          </div>

          {/* Footer stats */}
          <div className="grid grid-cols-3 gap-2 pt-2 border-t border-gray-800">
            <div>
              <p className="text-gray-500">Max loss</p>
              <p className="text-red-400">{edge.max_loss_pct.toFixed(1)}%</p>
            </div>
            <div>
              <p className="text-gray-500">Match score</p>
              <p className="text-white">{(edge.match_score * 100).toFixed(0)}%</p>
            </div>
            <div>
              <p className="text-gray-500">Detected</p>
              <p className="text-white">{timeSince(edge.detected_at)}</p>
            </div>
          </div>

          <p className="text-yellow-600/70 text-xs bg-yellow-900/20 rounded p-2 border border-yellow-800/30">
            {isOracleWeighted ? (
              <>
                Prob-weighted EV = {formatReturnPct(edge.weighted_ev_pct)} using{" "}
                <span className="text-blue-400/80">Pinnacle</span> as the true probability oracle
                (P_A={`${(edge.prob_leg_a * 100).toFixed(1)}%`}, P_B={`${(edge.prob_leg_b * 100).toFixed(1)}%`}).
                Only opportunities with positive Pinnacle-weighted EV are shown.
                Stakes are optimised so worst-case loss ≤ 10% of outlay.
              </>
            ) : (
              <>
                Avg return = {formatReturnPct(edge.avg_return_pct)} (simple 50/50 average of both payouts).
                Prob-weighted EV = {formatReturnPct(edge.weighted_ev_pct)} using each market&apos;s implied probability
                (P_A={`${(edge.prob_leg_a * 100).toFixed(1)}%`}, P_B={`${(edge.prob_leg_b * 100).toFixed(1)}%`}).
                Stakes are optimised so worst-case loss ≤ 10% of outlay.
              </>
            )}
          </p>
        </div>
      )}
    </div>
  );
}
