import type { OpportunitiesResponse, EvEdgesResponse, ValueResponse, SharpValueResponse, SportKeyInfo, FilterState, CryptoScanResult, NearCertaintyMarket } from "../types/arbitrage";

const BACKEND = import.meta.env.VITE_API_URL ?? "";
const BASE = `${BACKEND}/api`;

export async function fetchOpportunities(
  filters: Partial<FilterState> = {}
): Promise<OpportunitiesResponse> {
  const params = new URLSearchParams();
  if (filters.minProfit !== undefined)
    params.set("min_profit", String(filters.minProfit));
  filters.platforms?.forEach(p => params.append("platforms", p));
  filters.arbTypes?.forEach(t => params.append("arb_type", t));

  const res = await fetch(`${BASE}/opportunities?${params}`);
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}

export async function fetchEvEdges(
  minAvgReturnPct = 1.0
): Promise<EvEdgesResponse> {
  const params = new URLSearchParams();
  params.set("min_avg_return_pct", String(minAvgReturnPct));
  const res = await fetch(`${BASE}/ev-edges?${params}`);
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}

export async function fetchValueOps(
  minCrossEvPct = 2.0
): Promise<ValueResponse> {
  const params = new URLSearchParams();
  params.set("min_cross_ev_pct", String(minCrossEvPct));
  const res = await fetch(`${BASE}/value?${params}`);
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}

export async function triggerScan(): Promise<void> {
  await fetch(`${BASE}/scan/trigger`, { method: "POST" });
}

/** Fetch the cached result of the last sharp-value scan (no API requests consumed). */
export async function fetchSharpValueOps(): Promise<SharpValueResponse> {
  const res = await fetch(`${BASE}/sharp-value`);
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}

/**
 * Fetch sport key metadata enriched with live Kalshi event counts.
 * No Odds API requests consumed.
 */
export async function fetchSharpValueSports(): Promise<SportKeyInfo[]> {
  const res = await fetch(`${BASE}/sharp-value/sports`);
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}

/**
 * Force a fresh sharp-value scan using the Odds API Pinnacle oracle.
 * Pass sportKeys to scan only specific sports; omit to scan all.
 * Each sport key costs 1 Odds API request.
 */
export async function forceSharpValueScan(
  sportKeys?: string[]
): Promise<SharpValueResponse> {
  const res = await fetch(`${BASE}/sharp-value/scan`, {
    method:  "POST",
    headers: { "Content-Type": "application/json" },
    body:    JSON.stringify({ sport_keys: sportKeys ?? null }),
  });
  if (!res.ok) {
    const msg = await res.text().catch(() => String(res.status));
    throw new Error(`Sharp-value scan failed (${res.status}): ${msg}`);
  }
  return res.json();
}

/** Fetch the cached crypto scan result (last scheduled scan, no extra API calls). */
export async function fetchCryptoMarkets(): Promise<CryptoScanResult> {
  const res = await fetch(`${BASE}/crypto`);
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}

/** Force a fresh Kalshi crypto scan (no Odds API requests consumed). */
export async function forceCryptoScan(): Promise<CryptoScanResult> {
  const res = await fetch(`${BASE}/crypto/scan`, { method: "POST" });
  if (!res.ok) {
    const msg = await res.text().catch(() => String(res.status));
    throw new Error(`Crypto scan failed (${res.status}): ${msg}`);
  }
  return res.json();
}

/** Fetch all contracts currently priced at ≥ minProb % (default 97). */
export async function fetchNearCertainty(
  minProb = 97.0,
): Promise<{ markets: NearCertaintyMarket[]; count: number }> {
  const params = new URLSearchParams();
  params.set("min_prob", String(minProb));
  const res = await fetch(`${BASE}/near-certainty?${params}`);
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}

export function createWebSocket(): WebSocket {
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  const host = import.meta.env.DEV
    ? "localhost:8000"
    : (import.meta.env.VITE_API_URL
        ? import.meta.env.VITE_API_URL.replace(/^https?:\/\//, "")
        : window.location.host);
  return new WebSocket(`${protocol}://${host}/ws/opportunities`);
}
