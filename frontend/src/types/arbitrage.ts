export type Platform =
  | "kalshi"
  | "polymarket"
  | "predictit"
  | "odds_api"
  | "draftkings"
  | "fanduel"
  | "betmgm"
  | "caesars"
  | "pinnacle";

export type ContractSide = "yes" | "no";
export type ArbType = "cross_platform" | "sportsbook" | "spread" | "ev_edge" | "value";
export type SortKey = "net_profit" | "net_profit_pct" | "expected_value" | "detected_at";

export interface PlatformFees {
  profit_fee_pct:     number;
  trade_fee_pct:      number;
  withdrawal_fee_pct: number;
}

export interface MarketContract {
  platform:            Platform;
  market_id:           string;
  // New fields for correct event/outcome identification
  parent_event_id:     string;
  parent_event_title:  string;
  outcome_label:       string;   // e.g. "Yes", "No", "Republican", "Lakers"
  is_yes_side:         boolean;
  // Legacy fields
  event_title:         string;
  normalized_key:      string;
  side:                ContractSide;
  price:               number;
  payout_per_contract: number;
  decimal_odds:        number | null;
  volume_24h:          number | null;
  close_time:          string | null;
  url:                 string | null;
}

export interface ArbLeg {
  contract:        MarketContract;
  action:          string;  // "buy_yes", "buy_no", "buy_republican", etc.
  stake:           number;
  effective_cost:  number;
  expected_payout: number;
  platform_fees:   PlatformFees;
}

export interface ArbitrageOpportunity {
  id:               string;
  event_title:      string;
  leg_yes:          ArbLeg;
  leg_no:           ArbLeg;
  total_cost:       number;
  guaranteed_return: number;
  net_profit:       number;
  net_profit_pct:   number;
  expected_value:   number;
  match_score:      number;
  arb_type:         ArbType;
  detected_at:      string;
  expires_at:       string | null;
}

export interface ScannerStatus {
  platform:        Platform;
  last_scanned_at: string | null;
  markets_found:   number;
  error:           string | null;
  is_healthy:      boolean;
}

export interface OpportunitiesResponse {
  opportunities:  ArbitrageOpportunity[];
  scanner_status: ScannerStatus[];
  scanned_at:     string;
  total_markets:  number;
}

export interface EvEdgeOpportunity {
  id:                 string;
  event_title:        string;
  sb_leg:             ArbLeg;     // Leg A — sportsbook side
  pm_leg:             ArbLeg;     // Leg B — Kalshi/PM or second sportsbook
  sb_stake:           number;     // Optimal stake on leg A
  pm_stake:           number;     // Optimal stake on leg B
  total_cost:         number;     // sb_stake + pm_stake
  payout_if_sb_wins:  number;     // Total return if leg A wins
  payout_if_pm_wins:  number;     // Total return if leg B wins
  avg_return:         number;     // (payout_a + payout_b) / 2
  avg_return_pct:     number;     // (avg_return − total_cost) / total_cost × 100
  max_loss_pct:       number;     // Worst-case loss as % of total outlay (< 10%)
  ev_kalshi_pct:      number;     // Kalshi-oracle EV% (PM price as true prob)
  delta_pct:          number;     // |payout_a − payout_b| / total_cost × 100
  sb_fraction:        number;     // Fraction of total allocated to leg A (0–1)
  // Probability-weighted EV fields
  weighted_ev_pct:    number;     // Primary metric: (1 − prob_a − prob_b) × 100
  prob_leg_a:         number;     // Implied probability of leg A winning (0–1)
  prob_leg_b:         number;     // Implied probability of leg B winning (0–1)
  match_score:        number;
  arb_type:           "ev_edge";
  detected_at:        string;
  expires_at:         string | null;
}

export interface EvEdgesResponse {
  ev_edges:       EvEdgeOpportunity[];
  scanner_status: ScannerStatus[];
  scanned_at:     string;
  total_markets:  number;
}

/**
 * A single-leg sportsbook bet that is mispriced relative to Pinnacle's oracle
 * probability. Pinnacle's implied probability (1 / decimal_odds) for the SAME
 * outcome is used as the sharp-line reference.
 * Cross-market EV = (oracle_prob × decimal_odds − 1) × 100.
 */
export interface ValueOpportunity {
  id:              string;
  event_title:     string;
  sb_leg:          ArbLeg;        // The sportsbook bet (single leg)
  oracle_prob:     number;        // Oracle's implied probability for this SB outcome
  oracle_platform: Platform;      // Platform used as oracle (Pinnacle)
  sb_implied_prob: number;        // SB's own implied probability (1 / decimal_odds)
  cross_ev_pct:    number;        // (oracle_prob × D_a − 1) × 100
  edge_ppts:       number;        // oracle_prob − sb_implied_prob, in percentage points
  match_score:     number;
  arb_type:        "value";
  detected_at:     string;
  expires_at:      string | null;
}

export interface ValueResponse {
  value_ops:      ValueOpportunity[];
  scanner_status: ScannerStatus[];
  scanned_at:     string;
  total_markets:  number;
}

/**
 * Metadata for one Odds API sport key, enriched with the live Kalshi event count.
 * Returned by GET /api/sharp-value/sports.
 */
export interface SportKeyInfo {
  sport_key:          string;
  label:              string;
  group:              string;
  kalshi_slug:        string;
  kalshi_event_count: number;
}

/**
 * Response shape for the /api/sharp-value and /api/sharp-value/scan endpoints.
 * Uses the real Pinnacle oracle from The Odds API (force-scan-only, 500 req/month).
 */
export interface SharpValueResponse {
  value_ops:          ValueOpportunity[];
  requests_remaining: number;
  requests_used:      number;
  monthly_limit:      number;
  last_scan_at:       string | null;
  last_scan_cost:     number;
  scanner_status:     ScannerStatus[];
}

export interface WebSocketMessage {
  type: "opportunities_update" | "sharp_value_update" | "crypto_update" | "scanner_status" | "error";
  payload: {
    // Regular scan fields
    opportunities?:  ArbitrageOpportunity[];
    ev_edges?:       EvEdgeOpportunity[];
    value_ops?:      ValueOpportunity[];
    scanner_status?: ScannerStatus[];
    count?:          number;
    ev_edges_count?: number;
    value_ops_count?: number;
    // Sharp-value initial state (included in opportunities_update on WS connect)
    sharp_value_ops?:          ValueOpportunity[];
    sharp_value_remaining?:    number;
    sharp_value_last_scan_at?: string | null;
    // Sharp-value force-scan broadcast (type = "sharp_value_update")
    requests_remaining?: number;
    requests_used?:      number;
    monthly_limit?:      number;
    last_scan_at?:       string | null;
    last_scan_cost?:     number;
    // Crypto markets (included in opportunities_update + crypto_update broadcasts)
    crypto_markets?:    CryptoMarket[];
    crypto_arb_count?:  number;
    scanned_at?:        string | null;
  };
}

export interface FilterState {
  minProfit:  number;
  platforms:  Platform[];
  arbTypes:   ArbType[];
  sortBy:     SortKey;
}

/**
 * A crypto prediction market from Kalshi or Polymarket (YES/NO binary contract pair).
 * is_arb = true when yes_ask + no_ask < 1.00 — a guaranteed-profit opportunity.
 */
export interface CryptoMarket {
  platform:       "kalshi" | "polymarket" | string;
  event_ticker:   string;
  market_ticker:  string;
  asset:          "BTC" | "ETH" | "BCH" | string;
  market_type:    "15m" | "daily" | "weekly" | string;
  title:          string;
  close_time:     string;          // ISO timestamp
  floor_strike:   number;          // Reference price at window open
  yes_ask:        number;          // Cost of YES contract (0–1)
  no_ask:         number;          // Cost of NO contract (0–1)
  total_cost:     number;          // yes_ask + no_ask
  is_arb:         boolean;         // true when total_cost < 1.00
  net_profit_pct: number;          // (1 – total_cost) / total_cost × 100
  url:            string;
}

export interface CryptoScanResult {
  markets:    CryptoMarket[];
  arb_count:  number;
  scanned_at: string | null;       // ISO timestamp
}
