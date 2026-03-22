import type { Platform, ArbType } from "../types/arbitrage";

export const PLATFORM_LABELS: Record<Platform, string> = {
  kalshi:     "Kalshi",
  polymarket: "Polymarket",
  predictit:  "PredictIt",
  odds_api:   "Odds API",
  draftkings: "DraftKings",
  fanduel:    "FanDuel",
  betmgm:     "BetMGM",
  caesars:    "Caesars",
  pinnacle:   "Pinnacle",
};

export const ARB_TYPE_LABELS: Record<ArbType, string> = {
  cross_platform: "Guaranteed Arb",
  sportsbook:     "Sportsbook",
  spread:         "Guaranteed Arb",
  ev_edge:        "EV Edge",
  value:          "Value",
};

export function formatPlatform(platform: Platform): string {
  return PLATFORM_LABELS[platform] ?? platform;
}

export function formatDollars(value: number): string {
  return `$${value.toFixed(4)}`;
}

export function formatProfit(value: number): string {
  return `$${value.toFixed(4)}`;
}

export function formatPct(value: number): string {
  return `${value.toFixed(2)}%`;
}

export function formatEV(value: number): string {
  return `${value.toFixed(4)}x`;
}

export function formatTime(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString();
}

export function timeSince(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  if (diff < 60_000)  return `${Math.floor(diff / 1000)}s ago`;
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
  return `${Math.floor(diff / 3_600_000)}h ago`;
}
