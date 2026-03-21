import { useState, useCallback, useMemo, useEffect } from "react";
import { useWebSocket } from "./useWebSocket";
import { fetchOpportunities, triggerScan, forceSharpValueScan, fetchSharpValueSports, forceCryptoScan } from "../api/arbApi";
import type {
  ArbitrageOpportunity,
  EvEdgeOpportunity,
  ValueOpportunity,
  ScannerStatus,
  FilterState,
  WebSocketMessage,
  SortKey,
  SportKeyInfo,
  CryptoMarket,
} from "../types/arbitrage";

const DEFAULT_FILTERS: FilterState = {
  minProfit: 0.001,
  platforms: [],
  arbTypes:  [],
  sortBy:    "net_profit",
};

const SORT_FNS: Record<SortKey, (a: ArbitrageOpportunity, b: ArbitrageOpportunity) => number> = {
  net_profit:     (a, b) => b.net_profit     - a.net_profit,
  net_profit_pct: (a, b) => b.net_profit_pct - a.net_profit_pct,
  expected_value: (a, b) => b.expected_value  - a.expected_value,
  detected_at:    (a, b) =>
    new Date(b.detected_at).getTime() - new Date(a.detected_at).getTime(),
};

export function useArbitrageOpportunities() {
  const [allOpportunities, setAllOpportunities] = useState<ArbitrageOpportunity[]>([]);
  const [allEvEdges, setAllEvEdges]             = useState<EvEdgeOpportunity[]>([]);
  const [allValueOps, setAllValueOps]           = useState<ValueOpportunity[]>([]);
  const [scannerStatus, setScannerStatus]       = useState<ScannerStatus[]>([]);
  const [filters, setFilters]                   = useState<FilterState>(DEFAULT_FILTERS);
  const [lastUpdated, setLastUpdated]           = useState<Date | null>(null);
  const [isLoading, setIsLoading]               = useState(true);

  // ── Sharp-value (Odds API Pinnacle oracle) state ────────────────────────────
  const [allSharpValueOps, setAllSharpValueOps]               = useState<ValueOpportunity[]>([]);
  const [sharpRequestsRemaining, setSharpRequestsRemaining]   = useState<number>(500);
  const [sharpLastScanAt, setSharpLastScanAt]                 = useState<string | null>(null);
  const [isSharpScanning, setIsSharpScanning]                 = useState(false);
  const [sharpScanError, setSharpScanError]                   = useState<string | null>(null);
  const [sharpScannerStatus, setSharpScannerStatus]           = useState<ScannerStatus[]>([]);

  // ── Sport selection state ────────────────────────────────────────────────────
  const [sportInfo, setSportInfo]                             = useState<SportKeyInfo[]>([]);
  const [selectedSportKeys, setSelectedSportKeys]             = useState<string[]>([]);
  const [sportInfoLoading, setSportInfoLoading]               = useState(false);

  // ── Crypto state ─────────────────────────────────────────────────────────────
  const [cryptoMarkets, setCryptoMarkets]         = useState<CryptoMarket[]>([]);
  const [cryptoArbCount, setCryptoArbCount]       = useState(0);
  const [cryptoScannedAt, setCryptoScannedAt]     = useState<string | null>(null);
  const [isCryptoScanning, setIsCryptoScanning]   = useState(false);
  const [cryptoScanError, setCryptoScanError]     = useState<string | null>(null);

  const handleMessage = useCallback((msg: WebSocketMessage) => {
    if (msg.type === "opportunities_update") {
      setAllOpportunities(msg.payload.opportunities ?? []);
      setAllEvEdges(msg.payload.ev_edges ?? []);
      setAllValueOps(msg.payload.value_ops ?? []);
      setScannerStatus(msg.payload.scanner_status ?? []);
      setLastUpdated(new Date());
      setIsLoading(false);

      // Seed sharp-value state from the initial WS connect payload (if present)
      if (msg.payload.sharp_value_ops !== undefined) {
        setAllSharpValueOps(msg.payload.sharp_value_ops);
      }
      if (msg.payload.sharp_value_remaining !== undefined) {
        setSharpRequestsRemaining(msg.payload.sharp_value_remaining);
      }
      if (msg.payload.sharp_value_last_scan_at !== undefined) {
        setSharpLastScanAt(msg.payload.sharp_value_last_scan_at ?? null);
      }

      // Seed crypto state from every broadcast (scheduler runs crypto alongside arb scan)
      if (msg.payload.crypto_markets !== undefined) {
        setCryptoMarkets(msg.payload.crypto_markets);
      }
      if (msg.payload.crypto_arb_count !== undefined) {
        setCryptoArbCount(msg.payload.crypto_arb_count);
      }
    } else if (msg.type === "sharp_value_update") {
      // Broadcast fired by POST /api/sharp-value/scan
      setAllSharpValueOps(msg.payload.value_ops ?? []);
      if (msg.payload.requests_remaining !== undefined) {
        setSharpRequestsRemaining(msg.payload.requests_remaining);
      }
      if (msg.payload.last_scan_at !== undefined) {
        setSharpLastScanAt(msg.payload.last_scan_at ?? null);
      }
    } else if (msg.type === "crypto_update") {
      // Broadcast fired by POST /api/crypto/scan
      if (msg.payload.crypto_markets !== undefined) {
        setCryptoMarkets(msg.payload.crypto_markets);
      }
      if (msg.payload.crypto_arb_count !== undefined) {
        setCryptoArbCount(msg.payload.crypto_arb_count);
      }
      if (msg.payload.scanned_at !== undefined) {
        setCryptoScannedAt(msg.payload.scanned_at ?? null);
      }
    }
  }, []);

  const { isConnected } = useWebSocket(handleMessage);

  // Load sport metadata once on mount and pre-select sports with live Kalshi events
  useEffect(() => {
    setSportInfoLoading(true);
    fetchSharpValueSports()
      .then(info => {
        setSportInfo(info);
        // Default: select only sports that currently have matching Kalshi events
        const withEvents = info
          .filter(s => s.kalshi_event_count > 0)
          .map(s => s.sport_key);
        // If nothing has events yet (e.g. off-season), select everything
        setSelectedSportKeys(withEvents.length > 0 ? withEvents : info.map(s => s.sport_key));
      })
      .catch(() => {/* silently ignore — sport filter stays empty */})
      .finally(() => setSportInfoLoading(false));
  }, []);

  const refresh = useCallback(async () => {
    setIsLoading(true);
    try {
      const data = await fetchOpportunities(filters);
      setAllOpportunities(data.opportunities);
      setScannerStatus(data.scanner_status);
      setLastUpdated(new Date());
    } finally {
      setIsLoading(false);
    }
  }, [filters]);

  const forceScan = useCallback(async () => {
    await triggerScan();
  }, []);

  /** Trigger a Pinnacle sharp-value scan via the Odds API (1 req per sport key). */
  const forceSharpScan = useCallback(async () => {
    setIsSharpScanning(true);
    setSharpScanError(null);
    try {
      const keysToScan = selectedSportKeys.length > 0 ? selectedSportKeys : undefined;
      const result = await forceSharpValueScan(keysToScan);
      setAllSharpValueOps(result.value_ops);
      setSharpRequestsRemaining(result.requests_remaining);
      setSharpLastScanAt(result.last_scan_at);
      setSharpScannerStatus(result.scanner_status ?? []);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Unknown error";
      setSharpScanError(msg);
    } finally {
      setIsSharpScanning(false);
    }
  }, [selectedSportKeys]);

  /** Force a fresh Kalshi crypto scan (no Odds API requests consumed). */
  const forceCryptoScanNow = useCallback(async () => {
    setIsCryptoScanning(true);
    setCryptoScanError(null);
    try {
      const result = await forceCryptoScan();
      setCryptoMarkets(result.markets);
      setCryptoArbCount(result.arb_count);
      setCryptoScannedAt(result.scanned_at);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Unknown error";
      setCryptoScanError(msg);
    } finally {
      setIsCryptoScanning(false);
    }
  }, []);

  const opportunities = useMemo(() => {
    let out = [...allOpportunities];

    if (filters.minProfit > 0)
      out = out.filter(o => o.net_profit >= filters.minProfit);

    if (filters.platforms.length > 0)
      out = out.filter(o =>
        filters.platforms.includes(o.leg_yes.contract.platform) ||
        filters.platforms.includes(o.leg_no.contract.platform)
      );

    if (filters.arbTypes.length > 0)
      out = out.filter(o => filters.arbTypes.includes(o.arb_type));

    out.sort(SORT_FNS[filters.sortBy] ?? SORT_FNS.net_profit);

    return out;
  }, [allOpportunities, filters]);

  // EV edges sorted by avg_return_pct descending
  const evEdges = useMemo(
    () => [...allEvEdges].sort((a, b) => b.avg_return_pct - a.avg_return_pct),
    [allEvEdges]
  );

  // Value signals sorted by cross_ev_pct descending (highest Kalshi-oracle edge first)
  const valueOps = useMemo(
    () => [...allValueOps].sort((a, b) => b.cross_ev_pct - a.cross_ev_pct),
    [allValueOps]
  );

  // Sharp-value signals sorted by cross_ev_pct descending (same ordering as regular value)
  const sharpValueOps = useMemo(
    () => [...allSharpValueOps].sort((a, b) => b.cross_ev_pct - a.cross_ev_pct),
    [allSharpValueOps]
  );

  // Crypto markets: arbs first (best profit%), then by close_time ascending
  const cryptoMarketsOrdered = useMemo(
    () => [...cryptoMarkets].sort((a, b) => {
      if (a.is_arb !== b.is_arb) return a.is_arb ? -1 : 1;
      if (a.is_arb && b.is_arb) return b.net_profit_pct - a.net_profit_pct;
      return new Date(a.close_time).getTime() - new Date(b.close_time).getTime();
    }),
    [cryptoMarkets]
  );

  return {
    opportunities,
    evEdges,
    valueOps,
    scannerStatus,
    isConnected,
    isLoading,
    lastUpdated,
    filters,
    setFilters,
    refresh,
    forceScan,
    totalMarkets: scannerStatus.reduce((s, x) => s + x.markets_found, 0),
    // Sharp-value (Odds API Pinnacle oracle)
    sharpValueOps,
    sharpRequestsRemaining,
    sharpLastScanAt,
    isSharpScanning,
    sharpScanError,
    sharpScannerStatus,
    forceSharpScan,
    // Sport selection
    sportInfo,
    selectedSportKeys,
    setSelectedSportKeys,
    sportInfoLoading,
    // Crypto markets
    cryptoMarkets: cryptoMarketsOrdered,
    cryptoArbCount,
    cryptoScannedAt,
    isCryptoScanning,
    cryptoScanError,
    forceCryptoScan: forceCryptoScanNow,
  };
}
