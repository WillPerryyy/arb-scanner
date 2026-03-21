/**
 * CryptoTab
 *
 * Displays Kalshi crypto prediction markets (15-minute and daily) with real-time
 * intra-market arb detection.  An arb exists when yes_ask + no_ask < $1.00 —
 * buying both sides guarantees a $1.00 payout regardless of price direction.
 *
 * DraftKings Predictions shares Kalshi's underlying order book (it is a Kalshi
 * broker), so any intra-Kalshi arb is equally exploitable on DK Predictions.
 *
 * Data flow:
 *   • Background scheduler updates every 90s → WS broadcast → hook state
 *   • "Scan Now" button → POST /api/crypto/scan → WS "crypto_update" broadcast
 */
import { useState, useEffect, useCallback } from "react";
import type { CryptoMarket } from "../../types/arbitrage";

type AssetFilter      = "all" | "BTC" | "ETH" | "BCH";
type MarketTypeFilter = "all" | "15m" | "daily" | "weekly" | "cross";
type PlatformFilter   = "all" | "kalshi" | "polymarket" | "cross";

interface Props {
  markets:      CryptoMarket[];
  arbCount:     number;
  scannedAt:    string | null;
  isScanning:   boolean;
  scanError:    string | null;
  onScan:       () => void;
}

/** Format a price strike with appropriate precision. */
function formatStrike(n: number): string {
  if (n >= 1000) return `$${n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  return `$${n.toFixed(4)}`;
}

/** Countdown display: returns "Xm Ys" until the given ISO close_time. */
function useCountdown(closeTime: string): string {
  const [remaining, setRemaining] = useState(() => {
    const ms = new Date(closeTime).getTime() - Date.now();
    return Math.max(0, Math.floor(ms / 1000));
  });

  useEffect(() => {
    const id = setInterval(() => {
      const ms = new Date(closeTime).getTime() - Date.now();
      setRemaining(Math.max(0, Math.floor(ms / 1000)));
    }, 1000);
    return () => clearInterval(id);
  }, [closeTime]);

  if (remaining <= 0) return "closed";
  const m = Math.floor(remaining / 60);
  const s = remaining % 60;
  if (m >= 60) {
    const h = Math.floor(m / 60);
    const rm = m % 60;
    return `${h}h ${rm}m`;
  }
  return `${m}m ${s.toString().padStart(2, "0")}s`;
}

function MarketCard({ market }: { market: CryptoMarket }) {
  const countdown = useCountdown(market.close_time);

  const isExpiring = (() => {
    const ms = new Date(market.close_time).getTime() - Date.now();
    return ms > 0 && ms < 5 * 60 * 1000;
  })();

  const assetColor =
    market.asset === "BTC" ? "text-orange-400" :
    market.asset === "ETH" ? "text-blue-400"   :
    "text-gray-300";

  return (
    <div className={`rounded border p-4 space-y-3 transition-colors ${
      market.is_arb
        ? "border-green-700 bg-green-950/30"
        : "border-gray-800 bg-gray-900/40"
    }`}>
      {/* Header row */}
      <div className="flex items-start justify-between gap-2">
        <div className="space-y-0.5">
          <div className="flex items-center gap-2">
            <span className={`font-bold text-sm ${assetColor}`}>{market.asset}</span>
            <span className="text-xs px-1.5 py-0.5 rounded bg-gray-800 text-gray-400 uppercase">
              {market.market_type}
            </span>
            {market.is_arb && (
              <span className="text-xs px-1.5 py-0.5 rounded bg-green-900/60 text-green-400 border border-green-800/60 font-bold">
                ARB +{market.net_profit_pct.toFixed(2)}%
              </span>
            )}
          </div>
          <p className="text-xs text-gray-400 line-clamp-2">{market.title}</p>
        </div>
        <div className="flex items-center gap-1.5 shrink-0 mt-0.5">
            {market.platform === "polymarket" ? (
              <span className="text-xs px-1.5 py-0.5 rounded bg-purple-900/40 text-purple-400 border border-purple-800/50">
                poly
              </span>
            ) : market.platform === "cross" ? (
              <span className="text-xs px-1.5 py-0.5 rounded bg-yellow-900/40 text-yellow-400 border border-yellow-800/50">
                cross
              </span>
            ) : (
              <span className="text-xs px-1.5 py-0.5 rounded bg-gray-800 text-gray-500 border border-gray-700/50">
                klsh
              </span>
            )}
            <a
              href={market.url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-xs text-gray-600 hover:text-gray-400"
              title={`Open on ${market.platform === "polymarket" ? "Polymarket" : market.platform === "cross" ? "Kalshi (cross)" : "Kalshi"}`}
            >
              ↗
            </a>
          </div>
      </div>

      {/* Price grid */}
      <div className="grid grid-cols-3 gap-3 text-center">
        <div className="space-y-0.5">
          <div className="text-xs text-gray-500">YES ask</div>
          <div className="text-sm font-mono text-green-300">${market.yes_ask.toFixed(4)}</div>
        </div>
        <div className="space-y-0.5">
          <div className="text-xs text-gray-500">NO ask</div>
          <div className="text-sm font-mono text-red-300">${market.no_ask.toFixed(4)}</div>
        </div>
        <div className="space-y-0.5">
          <div className="text-xs text-gray-500">Total cost</div>
          <div className={`text-sm font-mono font-bold ${
            market.is_arb ? "text-green-400" : "text-gray-300"
          }`}>
            ${market.total_cost.toFixed(4)}
          </div>
        </div>
      </div>

      {/* Footer row */}
      <div className="flex items-center justify-between text-xs text-gray-500">
        <span>
          Strike: <span className="text-gray-300 font-mono">{formatStrike(market.floor_strike)}</span>
        </span>
        <span className={`font-mono ${isExpiring ? "text-amber-400" : ""}`}>
          {countdown === "closed" ? (
            <span className="text-red-500">closed</span>
          ) : (
            <>closes in <span className="text-gray-300">{countdown}</span></>
          )}
        </span>
      </div>
    </div>
  );
}

function EmptyState() {
  return (
    <div className="text-center py-12 text-gray-600">
      <div className="text-3xl mb-2">₿</div>
      <p className="text-sm">No crypto markets found</p>
      <p className="text-xs mt-1">
        Kalshi 15M markets are only open during active trading windows.
      </p>
    </div>
  );
}

function formatTimestamp(iso: string | null): string {
  if (!iso) return "never";
  try {
    return new Date(iso).toLocaleString(undefined, {
      month:  "short",
      day:    "numeric",
      hour:   "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch {
    return iso;
  }
}

export function CryptoTab({
  markets,
  arbCount,
  scannedAt,
  isScanning,
  scanError,
  onScan,
}: Props) {
  const [assetFilter, setAssetFilter]           = useState<AssetFilter>("all");
  const [marketTypeFilter, setMarketTypeFilter] = useState<MarketTypeFilter>("all");
  const [platformFilter, setPlatformFilter]     = useState<PlatformFilter>("all");

  const filtered = markets.filter(m => {
    if (assetFilter !== "all" && m.asset !== assetFilter) return false;
    if (marketTypeFilter !== "all" && m.market_type !== marketTypeFilter) return false;
    if (platformFilter !== "all" && m.platform !== platformFilter) return false;
    return true;
  });

  // Compute available assets and types from current markets for dynamic filter labels
  const assets = ["BTC", "ETH", "BCH"].filter(a =>
    markets.some(m => m.asset === a)
  );
  const hasDaily      = markets.some(m => m.market_type === "daily");
  const has15m        = markets.some(m => m.market_type === "15m");
  const hasWeekly     = markets.some(m => m.market_type === "weekly");
  const hasCross      = markets.some(m => m.market_type === "cross");
  const hasPolymarket = markets.some(m => m.platform === "polymarket");
  const hasKalshi     = markets.some(m => m.platform === "kalshi");
  const hasCrossPlatform = markets.some(m => m.platform === "cross");

  const arbsInView = filtered.filter(m => m.is_arb).length;

  return (
    <div className="space-y-4">
      {/* Header card */}
      <div className="rounded-lg border border-gray-800 bg-gray-900/40 p-4 space-y-3">
        {/* Title row */}
        <div className="flex items-center justify-between flex-wrap gap-2">
          <div>
            <h2 className="text-sm font-semibold text-white">
              Crypto Markets
              {arbCount > 0 && (
                <span className="ml-2 px-1.5 py-0.5 rounded-full text-xs bg-green-900/60 text-green-400 border border-green-800/60">
                  {arbCount} arb{arbCount !== 1 ? "s" : ""}
                </span>
              )}
            </h2>
            <p className="text-xs text-gray-500 mt-0.5">
              Kalshi 15-min &amp; daily · Polymarket weekly crypto markets
              {scannedAt && (
                <> · last scan {formatTimestamp(scannedAt)}</>
              )}
            </p>
          </div>
          <button
            onClick={onScan}
            disabled={isScanning}
            className={`text-xs px-3 py-1.5 rounded border transition-colors ${
              isScanning
                ? "border-gray-700 text-gray-500 cursor-not-allowed"
                : "bg-cyan-900/50 border-cyan-700 text-cyan-300 hover:bg-cyan-800/60"
            }`}
          >
            {isScanning ? "Scanning…" : "Scan Now"}
          </button>
        </div>

        {/* Stats row */}
        <div className="flex gap-4 text-xs">
          <span className="text-gray-400">
            <span className="text-white font-medium">{markets.length}</span> markets
          </span>
          <span className="text-gray-400">
            <span className={arbCount > 0 ? "text-green-400 font-medium" : "text-white font-medium"}>
              {arbCount}
            </span> guaranteed arbs
          </span>
          {filtered.length !== markets.length && (
            <span className="text-gray-400">
              showing <span className="text-white font-medium">{filtered.length}</span>
            </span>
          )}
        </div>

        {/* Arb explainer */}
        <p className="text-xs text-gray-600 leading-relaxed">
          An arb exists when YES ask + NO ask &lt; $1.00 on any single platform.
          Kalshi &amp; DK Predictions share the same order book.
          <span className="text-yellow-700"> Cross-platform arbs pair Kalshi and Polymarket markets with the same asset, strike, and resolution window — Polymarket prices are mid-market (last-traded), so verify actual ask prices before executing.</span>
        </p>
      </div>

      {/* Filters */}
      <div className="flex gap-2 flex-wrap">
        {/* Asset filter */}
        <div className="flex rounded border border-gray-800 overflow-hidden text-xs">
          {(["all", ...assets] as AssetFilter[]).map(a => (
            <button
              key={a}
              onClick={() => setAssetFilter(a)}
              className={`px-3 py-1.5 transition-colors ${
                assetFilter === a
                  ? "bg-gray-700 text-white"
                  : "text-gray-500 hover:text-gray-300 hover:bg-gray-800/60"
              }`}
            >
              {a === "all" ? "All assets" : a}
            </button>
          ))}
        </div>

        {/* Market type filter */}
        {(has15m || hasDaily || hasWeekly || hasCross) && (
          <div className="flex rounded border border-gray-800 overflow-hidden text-xs">
            {(["all", ...(has15m ? ["15m"] : []), ...(hasDaily ? ["daily"] : []), ...(hasWeekly ? ["weekly"] : []), ...(hasCross ? ["cross"] : [])] as MarketTypeFilter[]).map(t => (
              <button
                key={t}
                onClick={() => setMarketTypeFilter(t as MarketTypeFilter)}
                className={`px-3 py-1.5 transition-colors ${
                  marketTypeFilter === t
                    ? "bg-gray-700 text-white"
                    : "text-gray-500 hover:text-gray-300 hover:bg-gray-800/60"
                }`}
              >
                {t === "all" ? "All types" : t.toUpperCase()}
              </button>
            ))}
          </div>
        )}

        {/* Platform filter */}
        {(hasKalshi || hasPolymarket || hasCrossPlatform) && (
          <div className="flex rounded border border-gray-800 overflow-hidden text-xs">
            {(["all", ...(hasKalshi ? ["kalshi"] : []), ...(hasPolymarket ? ["polymarket"] : []), ...(hasCrossPlatform ? ["cross"] : [])] as PlatformFilter[]).map(p => (
              <button
                key={p}
                onClick={() => setPlatformFilter(p)}
                className={`px-3 py-1.5 transition-colors ${
                  platformFilter === p
                    ? "bg-gray-700 text-white"
                    : "text-gray-500 hover:text-gray-300 hover:bg-gray-800/60"
                }`}
              >
                {p === "all" ? "All sources" : p === "kalshi" ? "Kalshi" : p === "polymarket" ? "Polymarket" : "Cross"}
              </button>
            ))}
          </div>
        )}
      </div>

      {scanError && (
        <div className="rounded border border-red-800/50 bg-red-950/30 p-3 text-xs text-red-400">
          Scan error: {scanError}
        </div>
      )}

      {/* Market grid */}
      {filtered.length === 0 ? (
        <EmptyState />
      ) : (
        <>
          {arbsInView > 0 && (
            <p className="text-xs text-green-400">
              {arbsInView} arb{arbsInView !== 1 ? "s" : ""} in current view
            </p>
          )}
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {filtered.map(m => (
              <MarketCard key={m.market_ticker} market={m} />
            ))}
          </div>
        </>
      )}
    </div>
  );
}
