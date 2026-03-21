/**
 * SharpValueTab
 *
 * The "Value Test" tab — compares Kalshi vs the real Pinnacle oracle fetched
 * directly from The Odds API.  Unlike the regular Value tab (which uses the
 * Action Network Consensus line as a proxy), this uses true Pinnacle prices.
 *
 * Design constraints:
 *  • Force-scan-only: clicking "Force Scan" calls POST /api/sharp-value/scan.
 *  • 500 requests/month budget. Each scan costs 1 request per selected sport.
 *  • Request counter displayed prominently in the header.
 *  • Esports are not available on The Odds API — noted in the tab.
 *  • Reuses <ValueList> / <ValueCard> — zero UI duplication.
 *  • Sport selection panel with grouped checkboxes + live Kalshi event counts.
 */
import { useMemo } from "react";
import type { ValueOpportunity, SportKeyInfo, ScannerStatus } from "../../types/arbitrage";
import { ValueList } from "../Value/ValueList";
import { PLATFORM_LABELS } from "../../utils/formatters";

interface Props {
  valueOps:             ValueOpportunity[];
  requestsRemaining:    number;
  lastScanAt:           string | null;
  isScanning:           boolean;
  scanError:            string | null;
  scannerStatus:        ScannerStatus[];
  onForceScan:          () => void;
  sportInfo:            SportKeyInfo[];
  selectedSportKeys:    string[];
  setSelectedSportKeys: (keys: string[]) => void;
  sportInfoLoading:     boolean;
}

function formatTimestamp(iso: string | null): string {
  if (!iso) return "never";
  try {
    return new Date(iso).toLocaleString(undefined, {
      month:  "short",
      day:    "numeric",
      hour:   "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

export function SharpValueTab({
  valueOps,
  requestsRemaining,
  lastScanAt,
  isScanning,
  scanError,
  scannerStatus,
  onForceScan,
  sportInfo,
  selectedSportKeys,
  setSelectedSportKeys,
  sportInfoLoading,
}: Props) {
  const MONTHLY_LIMIT  = 500;
  const requestsUsed   = MONTHLY_LIMIT - requestsRemaining;
  const pctUsed        = Math.min((requestsUsed / MONTHLY_LIMIT) * 100, 100);
  const scanCost       = selectedSportKeys.length;       // 1 req per sport key
  const scansLeft      = scanCost > 0 ? Math.floor(requestsRemaining / scanCost) : 0;

  // Budget alert thresholds
  const budgetDanger  = requestsRemaining < 20;
  const budgetWarning = requestsRemaining < 50 && !budgetDanger;

  const budgetColor = budgetDanger
    ? "text-red-400"
    : budgetWarning
    ? "text-yellow-400"
    : "text-indigo-300";

  const budgetBarColor = budgetDanger
    ? "bg-red-500"
    : budgetWarning
    ? "bg-yellow-500"
    : "bg-indigo-500";

  // Group sports by their `group` field, preserving backend order
  const groupedSports = useMemo(() => {
    const map = new Map<string, SportKeyInfo[]>();
    for (const s of sportInfo) {
      if (!map.has(s.group)) map.set(s.group, []);
      map.get(s.group)!.push(s);
    }
    return map;
  }, [sportInfo]);

  const selectedSet = useMemo(() => new Set(selectedSportKeys), [selectedSportKeys]);

  function toggleSport(key: string) {
    if (selectedSet.has(key)) {
      setSelectedSportKeys(selectedSportKeys.filter(k => k !== key));
    } else {
      setSelectedSportKeys([...selectedSportKeys, key]);
    }
  }

  function selectGroup(group: string) {
    const groupKeys = (groupedSports.get(group) ?? []).map(s => s.sport_key);
    const next = new Set(selectedSportKeys);
    groupKeys.forEach(k => next.add(k));
    setSelectedSportKeys([...next]);
  }

  function deselectGroup(group: string) {
    const groupKeys = new Set((groupedSports.get(group) ?? []).map(s => s.sport_key));
    setSelectedSportKeys(selectedSportKeys.filter(k => !groupKeys.has(k)));
  }

  function selectAll() {
    setSelectedSportKeys(sportInfo.map(s => s.sport_key));
  }

  function selectNone() {
    setSelectedSportKeys([]);
  }

  const canScan = selectedSportKeys.length > 0 && !isScanning && requestsRemaining > 0;

  return (
    <div className="space-y-4">
      {/* ── Header card ─────────────────────────────────────────────────────── */}
      <div className="bg-gray-900 border border-indigo-900/50 rounded-xl p-4 space-y-3">
        {/* Title row */}
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="text-sm font-semibold text-indigo-300 flex items-center gap-2">
              <span>⚡</span>
              <span>Value Test — Real Pinnacle Oracle</span>
            </h2>
            <p className="text-xs text-gray-500 mt-0.5">
              Compares Kalshi vs live Pinnacle lines from The Odds API.
              Spread &amp; total markets included.
            </p>
          </div>

          {/* Force Scan button */}
          <button
            onClick={onForceScan}
            disabled={!canScan}
            title={
              selectedSportKeys.length === 0
                ? "Select at least one sport to scan"
                : requestsRemaining === 0
                ? "Monthly request quota exhausted"
                : undefined
            }
            className={`shrink-0 text-xs px-3 py-1.5 rounded border transition-colors
              ${isScanning
                ? "bg-indigo-900/30 border-indigo-700 text-indigo-400 cursor-wait"
                : !canScan
                ? "bg-gray-800 border-gray-700 text-gray-600 cursor-not-allowed"
                : "bg-indigo-900/50 border-indigo-700 text-indigo-300 hover:bg-indigo-800/60"
              }`}
          >
            {isScanning ? (
              <span className="flex items-center gap-1.5">
                <svg
                  className="animate-spin h-3 w-3"
                  xmlns="http://www.w3.org/2000/svg"
                  fill="none"
                  viewBox="0 0 24 24"
                >
                  <circle
                    className="opacity-25"
                    cx="12" cy="12" r="10"
                    stroke="currentColor" strokeWidth="4"
                  />
                  <path
                    className="opacity-75"
                    fill="currentColor"
                    d="M4 12a8 8 0 018-8v4l3-3-3-3v4a8 8 0 00-8 8h4z"
                  />
                </svg>
                Scanning…
              </span>
            ) : "Force Scan"}
          </button>
        </div>

        {/* Request quota bar */}
        <div className="space-y-1">
          <div className="flex items-center justify-between text-xs">
            <span className="text-gray-500">Monthly quota</span>
            <span className={`font-semibold tabular-nums ${budgetColor}`}>
              {requestsRemaining.toLocaleString()} / {MONTHLY_LIMIT.toLocaleString()} remaining
              {budgetDanger && " ⚠ low!"}
              {budgetWarning && " ⚠"}
            </span>
          </div>
          <div className="w-full bg-gray-800 rounded-full h-1.5 overflow-hidden">
            <div
              className={`${budgetBarColor} h-1.5 rounded-full transition-all duration-500`}
              style={{ width: `${100 - pctUsed}%` }}
            />
          </div>
          <p className="text-xs text-gray-600">
            {scanCost > 0
              ? `${scanCost} sport${scanCost !== 1 ? "s" : ""} selected · ~${scanCost} requests per scan · ~${scansLeft} scans left this month`
              : "Select sports below to see request cost"}
          </p>
        </div>

        {/* Scanner status pills — shown after scan completes */}
        {(isScanning || scannerStatus.length > 0) && (
          <div className="flex flex-wrap gap-1.5 border-t border-gray-800 pt-2">
            {isScanning ? (
              <span className="text-xs text-gray-600 italic">Fetching data feeds…</span>
            ) : (
              scannerStatus.map(s => (
                <div
                  key={s.platform}
                  title={s.error ?? undefined}
                  className={`flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium
                    ${s.is_healthy
                      ? "bg-green-900/40 text-green-300 border border-green-800/60"
                      : "bg-red-900/40 text-red-300 border border-red-800/60"
                    }`}
                >
                  <span className={`w-1.5 h-1.5 rounded-full ${s.is_healthy ? "bg-green-400" : "bg-red-400"}`} />
                  {PLATFORM_LABELS[s.platform] ?? s.platform}
                  <span className="opacity-50">·</span>
                  <span className="opacity-80">{s.markets_found.toLocaleString()}</span>
                </div>
              ))
            )}
          </div>
        )}

        {/* Last scan / esports note */}
        <div className="flex items-center justify-between text-xs text-gray-600 border-t border-gray-800 pt-2">
          <span>Last scan: {formatTimestamp(lastScanAt)}</span>
          <span className="text-gray-700">Esports not available via Odds API</span>
        </div>
      </div>

      {/* ── Sport selection panel ─────────────────────────────────────────────── */}
      <div className="bg-gray-900 border border-gray-800 rounded-xl p-4 space-y-3">
        {/* Panel header with global All / None */}
        <div className="flex items-center justify-between">
          <span className="text-xs font-semibold text-gray-400 uppercase tracking-wider">
            Sports to scan
          </span>
          <div className="flex items-center gap-2 text-xs">
            <span className="text-gray-600">
              {selectedSportKeys.length} / {sportInfo.length} selected
            </span>
            <button
              onClick={selectAll}
              className="text-indigo-400 hover:text-indigo-300 transition-colors"
            >
              All
            </button>
            <span className="text-gray-700">·</span>
            <button
              onClick={selectNone}
              className="text-gray-500 hover:text-gray-400 transition-colors"
            >
              None
            </button>
          </div>
        </div>

        {sportInfoLoading ? (
          <p className="text-xs text-gray-600 py-2">Loading sports…</p>
        ) : sportInfo.length === 0 ? (
          <p className="text-xs text-gray-600 py-2">
            Sport list unavailable — run a background scan first.
          </p>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-4">
            {[...groupedSports.entries()].map(([group, sports]) => {
              const groupSelected = sports.filter(s => selectedSet.has(s.sport_key)).length;
              const groupTotal    = sports.length;

              return (
                <div key={group} className="space-y-1.5">
                  {/* Group header */}
                  <div className="flex items-center justify-between pb-0.5 border-b border-gray-800">
                    <span className="text-xs font-medium text-gray-400">{group}</span>
                    <div className="flex items-center gap-1.5 text-xs">
                      <span className="text-gray-700 tabular-nums">
                        {groupSelected}/{groupTotal}
                      </span>
                      <button
                        onClick={() => selectGroup(group)}
                        className="text-indigo-500 hover:text-indigo-400 transition-colors"
                      >
                        All
                      </button>
                      <span className="text-gray-800">·</span>
                      <button
                        onClick={() => deselectGroup(group)}
                        className="text-gray-600 hover:text-gray-500 transition-colors"
                      >
                        None
                      </button>
                    </div>
                  </div>

                  {/* Sport rows */}
                  {sports.map(sport => {
                    const checked  = selectedSet.has(sport.sport_key);
                    const hasEvents = sport.kalshi_event_count > 0;

                    return (
                      <label
                        key={sport.sport_key}
                        className={`flex items-center gap-2 cursor-pointer group
                          ${hasEvents ? "" : "opacity-40"}`}
                      >
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={() => toggleSport(sport.sport_key)}
                          className="w-3.5 h-3.5 rounded border-gray-600 bg-gray-800
                                     accent-indigo-500 cursor-pointer"
                        />
                        <span
                          className={`text-xs flex-1 transition-colors
                            ${checked
                              ? "text-gray-200 group-hover:text-white"
                              : "text-gray-500 group-hover:text-gray-400"
                            }`}
                        >
                          {sport.label}
                        </span>
                        <span
                          className={`text-xs tabular-nums shrink-0
                            ${hasEvents
                              ? checked ? "text-indigo-400" : "text-gray-600"
                              : "text-gray-700"
                            }`}
                        >
                          ({sport.kalshi_event_count})
                        </span>
                      </label>
                    );
                  })}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* ── Error banner ─────────────────────────────────────────────────────── */}
      {scanError && (
        <div className="bg-red-900/20 border border-red-800/50 rounded-lg p-3 text-xs text-red-400">
          <strong>Scan error:</strong> {scanError}
        </div>
      )}

      {/* ── Results ──────────────────────────────────────────────────────────── */}
      {!lastScanAt && !isScanning ? (
        <div className="text-center py-16 text-gray-600">
          <p className="text-2xl mb-2">🔬</p>
          <p className="font-medium text-gray-500">No scan yet</p>
          <p className="text-xs mt-1 max-w-sm mx-auto">
            Select the sports you want to check, then click{" "}
            <span className="text-indigo-400">Force Scan</span> to fetch live
            Pinnacle lines and compare against Kalshi prices.
          </p>
        </div>
      ) : (
        <ValueList
          valueOps={valueOps}
          allPlatforms={["kalshi"]}
          enabledPlatforms={["kalshi"]}
          onTogglePlatform={() => {/* sharp-value is Kalshi-only, no toggle needed */}}
        />
      )}
    </div>
  );
}
