import { useState, useMemo } from "react";
import type { ArbitrageOpportunity, ArbLeg, Platform } from "../../types/arbitrage";
import {
  formatPlatform, formatProfit, formatDollars,
  formatPct, formatEV, timeSince, ARB_TYPE_LABELS,
} from "../../utils/formatters";
import { rescaleOpportunity } from "../../utils/rescale";
import { Badge } from "../UI/Badge";

interface Props {
  opp: ArbitrageOpportunity;
}

const ARB_TYPE_BADGE: Record<string, "info" | "success" | "warning" | "purple"> = {
  cross_platform: "success",
  sportsbook:     "warning",
  spread:         "success",
};

/**
 * Platforms that are traditional sportsbooks (moneyline decimal odds).
 * Kalshi also stores decimal_odds (as 1/price), so we must use platform
 * identity — NOT `decimal_odds !== null` — to distinguish sportsbook legs.
 */
const SPORTSBOOK_PLATFORMS: Set<Platform> = new Set([
  "draftkings", "fanduel", "betmgm", "caesars",
]);

/** Returns true if this leg belongs to a sportsbook (not a prediction market). */
function isLegSportsbook(leg: ArbLeg): boolean {
  return SPORTSBOOK_PLATFORMS.has(leg.contract.platform);
}

function isSpreadSell(leg: ArbLeg): boolean {
  return leg.action.startsWith("sell_");
}

function getLegOutcomeLabel(leg: ArbLeg): string {
  const label = leg.contract.outcome_label;
  if (label && label.toLowerCase() !== "yes" && label.toLowerCase() !== "no") {
    return label;
  }
  if (leg.action.includes("yes")) return "YES";
  if (leg.action.includes("no"))  return "NO";
  return label || "—";
}

function SpreadLegCard({
  leg,
  label,
  isSell,
  isSportsbook,
}: {
  leg: ArbLeg;
  label: string;
  isSell: boolean;
  isSportsbook: boolean;
}) {
  const borderColor = isSell
    ? "bg-orange-900/20 border-orange-800/40"
    : "bg-green-900/20 border-green-800/40";
  const textColor = isSell ? "text-orange-400" : "text-green-400";

  // Resolve the equivalent "BUY X" label for this sell leg:
  //   1. Backend provides opponent abbreviation (e.g. sell OKC YES → buy DAL)
  //   2. For YES/NO binary contracts: sell NO → buy YES, sell YES → buy NO
  const equivLabel = isSell
    ? (leg.equivalent_buy_label
        ?? (label === "NO" ? "YES" : label === "YES" ? "NO" : null))
    : null;

  return (
    <div className={`rounded-lg p-3 space-y-1.5 border text-xs ${borderColor}`}>
      <p className={`font-semibold ${textColor}`}>
        {isSell
          ? equivLabel
            ? `BUY ${equivLabel}`
            : `BUY — ${label}`
          : `BUY — ${label}`
        }
      </p>
      <p className="text-gray-400">
        Platform: <span className="text-white">{formatPlatform(leg.contract.platform)}</span>
      </p>
      {leg.contract.event_title && (
        <p className="text-gray-400">
          Contract:{" "}
          <span className="text-white text-xs">
            {leg.contract.event_title.slice(0, 60)}
            {leg.contract.event_title.length > 60 ? "…" : ""}
          </span>
        </p>
      )}
      <p className="text-gray-400">
        Price: <span className="text-white">{formatDollars(leg.contract.price)}</span>
        {leg.contract.decimal_odds != null && (
          <span className="text-gray-500 ml-2">
            ({leg.contract.decimal_odds >= 2
              ? `+${Math.round((leg.contract.decimal_odds - 1) * 100)}`
              : `-${Math.round(100 / (leg.contract.decimal_odds - 1))}`})
          </span>
        )}
      </p>
      {isSell ? (
        <>
          <p className="text-gray-400">
            Contracts:{" "}
            <span className="text-white font-medium">{leg.stake.toFixed(2)}</span>
          </p>
          <p className="text-gray-400">
            Premium collected:{" "}
            <span className="text-green-400 font-medium">
              +{formatDollars(leg.stake * leg.contract.price)}
            </span>
          </p>
          <p className="text-gray-400">
            Max loss (collateral):{" "}
            <span className="text-orange-400 font-medium">
              {formatDollars(leg.effective_cost)}
            </span>
          </p>
        </>
      ) : isSportsbook ? (
        <>
          <p className="text-gray-400">
            Stake:{" "}
            <span className="text-white font-medium">${leg.stake.toFixed(2)}</span>
          </p>
          <p className="text-gray-400">
            Total return if win:{" "}
            <span className="text-green-400 font-medium">
              ${leg.expected_payout.toFixed(2)}
            </span>
          </p>
        </>
      ) : (
        <>
          <p className="text-gray-400">
            Stake:{" "}
            <span className="text-white font-medium">{formatDollars(leg.stake)}</span>
          </p>
          <p className="text-gray-400">
            Effective cost:{" "}
            <span className="text-white font-medium">{formatDollars(leg.effective_cost)}</span>
          </p>
        </>
      )}
      {leg.contract.url && (
        <a
          href={leg.contract.url}
          target="_blank"
          rel="noreferrer"
          className={`inline-block mt-1 underline hover:opacity-80 ${isSell ? "text-orange-400" : "text-green-400"}`}
        >
          Open market →
        </a>
      )}
    </div>
  );
}

function HedgeLegCard({
  leg,
  index,
}: {
  leg: ArbLeg;
  index: number;
}) {
  const isSb = isLegSportsbook(leg);
  const borderColor = index === 0
    ? "bg-green-900/20 border-green-800/40"
    : "bg-blue-900/20 border-blue-800/40";
  const textColor = index === 0 ? "text-green-400" : "text-blue-400";
  const linkColor = index === 0 ? "text-green-400" : "text-blue-400";
  const label = getLegOutcomeLabel(leg);

  // For Kalshi (non-sportsbook) legs: shares = effective_cost / price_per_share
  const sharesCount = !isSb && leg.contract.price > 0
    ? (leg.effective_cost / leg.contract.price)
    : null;

  return (
    <div className={`rounded-lg p-3 space-y-1.5 border text-xs ${borderColor}`}>
      <p className={`font-semibold ${textColor}`}>
        {index === 0 ? "Leg A" : "Leg B"}: BUY {label}
      </p>
      <p className="text-gray-400">
        Platform: <span className="text-white">{formatPlatform(leg.contract.platform)}</span>
      </p>
      {leg.contract.event_title && leg.contract.event_title !== leg.contract.parent_event_title && (
        <p className="text-gray-400">
          Contract:{" "}
          <span className="text-white text-xs">
            {leg.contract.event_title.slice(0, 60)}
            {leg.contract.event_title.length > 60 ? "…" : ""}
          </span>
        </p>
      )}

      {/* Sportsbook leg: show stake + american odds + win payout */}
      {isSb ? (
        <>
          <p className="text-gray-400">
            Stake: <span className="text-white font-medium">${leg.effective_cost.toFixed(2)}</span>
          </p>
          {leg.contract.decimal_odds != null && (
            <p className="text-gray-400">
              Odds:{" "}
              <span className="text-white">
                {leg.contract.decimal_odds >= 2
                  ? `+${Math.round((leg.contract.decimal_odds - 1) * 100)}`
                  : `-${Math.round(100 / (leg.contract.decimal_odds - 1))}`}
              </span>
              <span className="text-gray-500 ml-1">
                ({leg.contract.decimal_odds.toFixed(3)}x)
              </span>
            </p>
          )}
          <p className="text-gray-400">
            Return if win:{" "}
            <span className="text-green-400 font-medium">${leg.expected_payout.toFixed(2)}</span>
          </p>
        </>
      ) : (
        /* Prediction market leg: show price/share + shares count + total cost */
        <>
          <p className="text-gray-400">
            Price/share: <span className="text-white">{formatDollars(leg.contract.price)}</span>
          </p>
          {sharesCount !== null && (
            <p className="text-gray-400">
              Shares to buy:{" "}
              <span className="text-white font-medium">{sharesCount.toFixed(2)}</span>
            </p>
          )}
          <p className="text-gray-400">
            Total cost:{" "}
            <span className="text-white font-medium">{formatDollars(leg.effective_cost)}</span>
          </p>
          <p className="text-gray-400">
            Payout if win:{" "}
            <span className="text-green-400 font-medium">${leg.expected_payout.toFixed(2)}</span>
          </p>
        </>
      )}

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

export function OpportunityCard({ opp }: Props) {
  const [expanded, setExpanded] = useState(false);
  // Store as string to allow partial edits (e.g. "23." with trailing decimal)
  const [outlayInput, setOutlayInput] = useState<string>("");

  const isSpread    = opp.arb_type === "spread";
  const isSportsbook = opp.arb_type === "sportsbook";

  // Compute displayed opportunity — scaled to custom outlay if provided
  const displayOpp: ArbitrageOpportunity = useMemo(() => {
    const parsed = parseFloat(outlayInput);
    if (!outlayInput || isNaN(parsed) || parsed <= 0) return opp;
    return rescaleOpportunity(opp, parsed);
  }, [opp, outlayInput]);

  const legA = displayOpp.leg_yes;
  const legB = displayOpp.leg_no;

  // For spread arbs: identify which leg is the sell leg
  const aSell = isSpread && isSpreadSell(legA);
  const bSell = isSpread && isSpreadSell(legB);
  const buyLeg  = aSell ? legB : legA;
  const sellLeg = aSell ? legA : legB;

  const buyLabel  = getLegOutcomeLabel(buyLeg);
  const sellLabel = getLegOutcomeLabel(sellLeg);

  const isSbBuy = isLegSportsbook(buyLeg);

  // Whether a custom outlay is actively set
  const isScaled = !!outlayInput && !isNaN(parseFloat(outlayInput)) && parseFloat(outlayInput) > 0;

  return (
    <div
      onClick={() => setExpanded(e => !e)}
      className="bg-gray-900 border border-gray-800 rounded-xl p-4 cursor-pointer
                 hover:border-green-700 transition-colors select-none"
    >
      {/* Header */}
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <p className="font-semibold text-white text-sm leading-snug">
            {displayOpp.event_title}
          </p>
          <div className="flex items-center gap-1.5 mt-1.5 flex-wrap text-xs">
            {isSpread ? (
              <>
                {/* Buy leg */}
                <span className="text-gray-400">
                  {formatPlatform(buyLeg.contract.platform)}{" "}
                  <span className="text-green-400 font-semibold">BUY {buyLabel}</span>
                </span>
                <span className="text-gray-600">+</span>
                {/* Sell leg — always shown as equivalent BUY position */}
                <span className="text-gray-400">
                  {formatPlatform(sellLeg.contract.platform)}{" "}
                  {(() => {
                    const equiv = sellLeg.equivalent_buy_label
                      ?? (sellLabel === "NO" ? "YES" : sellLabel === "YES" ? "NO" : null);
                    return equiv ? (
                      <span className="text-orange-400 font-semibold">BUY {equiv}</span>
                    ) : (
                      <span className="text-orange-400 font-semibold">BUY {sellLabel}</span>
                    );
                  })()}
                </span>
              </>
            ) : (
              <>
                <span className="text-gray-400">
                  {formatPlatform(legA.contract.platform)}{" "}
                  <span className="text-green-400 font-semibold">{getLegOutcomeLabel(legA)}</span>
                </span>
                <span className="text-gray-600">+</span>
                <span className="text-gray-400">
                  {formatPlatform(legB.contract.platform)}{" "}
                  <span className="text-blue-400 font-semibold">{getLegOutcomeLabel(legB)}</span>
                </span>
              </>
            )}
            <Badge variant={ARB_TYPE_BADGE[displayOpp.arb_type] ?? "info"}>
              {ARB_TYPE_LABELS[displayOpp.arb_type]}
            </Badge>
          </div>
        </div>

        {/* Profit summary */}
        <div className="text-right shrink-0">
          <p className="text-green-400 font-bold text-base">
            +{formatProfit(displayOpp.net_profit)}
          </p>
          <p className="text-xs text-gray-400">
            {formatPct(displayOpp.net_profit_pct)} ROI
          </p>
          {isScaled && (
            <p className="text-xs text-blue-400 mt-0.5">scaled</p>
          )}
        </div>
      </div>

      {/* Key metrics row */}
      <div className="mt-3 grid grid-cols-2 sm:grid-cols-4 gap-2 text-xs">
        {isSpread ? (
          <>
            <div>
              <p className="text-gray-500">
                {isSbBuy ? "SB stake" : "Buy price"}
              </p>
              <p className="text-white font-medium">
                {isSbBuy
                  ? `$${buyLeg.stake.toFixed(2)}`
                  : formatDollars(buyLeg.stake)}
              </p>
            </div>
            <div>
              <p className="text-gray-500">Contracts</p>
              <p className="text-white font-medium">
                {sellLeg.stake.toFixed(2)}
              </p>
            </div>
            <div>
              <p className="text-gray-500">Total outlay</p>
              <p className="text-white font-medium">
                {formatDollars(displayOpp.total_cost)}
              </p>
            </div>
            <div>
              <p className="text-gray-500">Profit</p>
              <p className="text-green-300 font-medium">
                +{formatDollars(displayOpp.net_profit)}
              </p>
            </div>
          </>
        ) : (
          <>
            <div>
              <p className="text-gray-500">Leg A</p>
              <p className="text-white font-medium">
                ${legA.effective_cost.toFixed(2)}
              </p>
            </div>
            <div>
              <p className="text-gray-500">Leg B</p>
              <p className="text-white font-medium">
                ${legB.effective_cost.toFixed(2)}
              </p>
            </div>
            <div>
              <p className="text-gray-500">Total cost</p>
              <p className="text-white font-medium">
                ${displayOpp.total_cost.toFixed(2)}
              </p>
            </div>
            <div>
              <p className="text-gray-500">Guaranteed</p>
              <p className="text-green-300 font-medium">
                ${displayOpp.guaranteed_return.toFixed(2)}
              </p>
            </div>
          </>
        )}
      </div>

      {/* Expanded detail */}
      {expanded && (
        <div
          onClick={e => e.stopPropagation()}
          className="mt-4 border-t border-gray-800 pt-4 space-y-3 text-xs"
        >
          {/* ── Outlay rescaler ──────────────────────────────────────────── */}
          <div className="flex items-center gap-2 bg-gray-800/60 rounded-lg p-3">
            <span className="text-gray-300 text-xs font-medium">Total outlay $</span>
            <input
              type="number"
              min="0.01"
              step="1"
              value={outlayInput}
              onChange={e => setOutlayInput(e.target.value)}
              onClick={e => e.stopPropagation()}
              placeholder={opp.total_cost.toFixed(2)}
              className="w-28 bg-gray-700 border border-gray-600 rounded px-2 py-1
                         text-white text-xs focus:outline-none focus:border-green-500
                         placeholder-gray-500"
            />
            <span className="text-gray-500 text-xs">
              base: ${opp.total_cost.toFixed(2)}
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

          {isSpread ? (
            <>
              {/* Spread arb — buy + sell breakdown */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <SpreadLegCard
                  leg={buyLeg}
                  label={buyLabel}
                  isSell={false}
                  isSportsbook={isSbBuy}
                />
                <SpreadLegCard
                  leg={sellLeg}
                  label={sellLabel}
                  isSell={true}
                  isSportsbook={false}
                />
              </div>

              {/* Outcome scenarios */}
              <div className="bg-gray-800/50 rounded-lg p-3 space-y-2">
                <p className="text-gray-400 font-semibold">Outcome scenarios</p>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <div>
                    <p className="text-gray-500">{buyLabel} wins</p>
                    <p className="text-green-400 font-medium">
                      +{formatDollars(displayOpp.net_profit)}
                    </p>
                    <p className="text-gray-600 text-xs">
                      Buy leg pays ${buyLeg.expected_payout.toFixed(2)}, collateral released
                    </p>
                  </div>
                  <div>
                    <p className="text-gray-500">{buyLabel} loses</p>
                    <p className="text-green-400 font-medium">
                      +{formatDollars(displayOpp.net_profit)}
                    </p>
                    <p className="text-gray-600 text-xs">
                      Buy leg worthless, keep premium collected — same net
                    </p>
                  </div>
                </div>
              </div>
            </>
          ) : (
            <>
              {/* Hedge arb — buy-buy two-leg breakdown */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <HedgeLegCard leg={legA} index={0} />
                <HedgeLegCard leg={legB} index={1} />
              </div>

              {/* Outcome scenarios */}
              <div className="bg-gray-800/50 rounded-lg p-3 space-y-2">
                <p className="text-gray-400 font-semibold">Outcome scenarios</p>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <div>
                    <p className="text-gray-500">{getLegOutcomeLabel(legA)} wins</p>
                    <p className="text-green-400 font-medium">
                      +{formatDollars(displayOpp.net_profit)}
                    </p>
                    <p className="text-gray-600 text-xs">
                      Leg A pays ${legA.expected_payout.toFixed(2)}, Leg B worthless
                    </p>
                  </div>
                  <div>
                    <p className="text-gray-500">{getLegOutcomeLabel(legB)} wins</p>
                    <p className="text-green-400 font-medium">
                      +{formatDollars(displayOpp.net_profit)}
                    </p>
                    <p className="text-gray-600 text-xs">
                      Leg B pays ${legB.expected_payout.toFixed(2)}, Leg A worthless
                    </p>
                  </div>
                </div>
              </div>

              {isSportsbook && (
                <p className="text-yellow-600/80 text-xs bg-yellow-900/20 rounded p-2 border border-yellow-800/30">
                  Sportsbook arb: use the outlay input above to scale stakes to your desired bet size.
                </p>
              )}
            </>
          )}

          {/* Summary stats */}
          <div className="grid grid-cols-3 gap-2 pt-2 border-t border-gray-800">
            <div>
              <p className="text-gray-500">Expected Value</p>
              <p className="text-blue-400 font-semibold">{formatEV(displayOpp.expected_value)}</p>
            </div>
            <div>
              <p className="text-gray-500">Match Score</p>
              <p className="text-white">{(displayOpp.match_score * 100).toFixed(0)}%</p>
            </div>
            <div>
              <p className="text-gray-500">Detected</p>
              <p className="text-white">{timeSince(opp.detected_at)}</p>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
