from __future__ import annotations
from enum import Enum
from datetime import datetime
from typing import Optional, Union
from pydantic import BaseModel, Field


class Platform(str, Enum):
    KALSHI      = "kalshi"
    POLYMARKET  = "polymarket"
    PREDICTIT   = "predictit"
    ODDS_API    = "odds_api"
    DRAFTKINGS  = "draftkings"
    FANDUEL     = "fanduel"
    BETMGM      = "betmgm"
    CAESARS     = "caesars"
    PINNACLE    = "pinnacle"


class ContractSide(str, Enum):
    YES = "yes"
    NO  = "no"


class MarketContract(BaseModel):
    """
    Represents ONE purchasable position on ONE platform.

    Key fields for the new matching system:
    - parent_event_id:  canonical ID for the underlying event (e.g. "will-X-happen",
                        "NBA Lakers vs Celtics 2025-02-20"). Contracts that share a
                        parent_event_id are mutually-exclusive outcomes of the same event.
    - outcome_label:    human-readable outcome this contract pays for (e.g. "Yes", "No",
                        "Lakers", "Celtics", "Republican", "Democrat").
    - is_yes_side:      True  → this is the YES/affirmative/home/favourite leg.
                        False → this is the NO/negative/away/underdog leg.
                        For binary markets (YES/NO) both legs of the same market
                        are complementary — buying both guarantees a payout.
                        For multi-outcome markets each outcome is its own contract.

    The arbitrage engine pairs contracts that:
      1. Belong to the SAME underlying event (fuzzy-matched parent_event_id).
      2. Are DIFFERENT outcomes (so exactly one will pay out).
      3. Have a combined cost < the guaranteed payout.
    """
    platform:               Platform
    market_id:              str          # Platform-specific market/contract identifier
    # --- event grouping ---
    parent_event_id:        str = ""     # Normalised event key for cross-platform matching
    parent_event_title:     str = ""     # Human-readable parent event name
    outcome_label:          str = ""     # What outcome this contract wins on ("Yes","Lakers",…)
    is_yes_side:            bool = True  # True = YES/home/first-outcome; False = NO/away
    # --- pricing ---
    event_title:            str          # Full display title (may include outcome)
    normalized_key:         str = ""     # Deprecated; kept for backwards compat
    side:                   ContractSide # Kept for backwards compat; derived from is_yes_side
    price:                  float        # Cost to buy 1 contract share (0–1 for pred markets)
    payout_per_contract:    float = 1.0  # What a winning share pays out
    decimal_odds:           Optional[float] = None  # For sportsbook legs
    num_outcomes:           int = 2                 # 2 for binary, 3 for soccer (Home/Draw/Away)
    market_type:            str = "moneyline"       # "moneyline" | "spread" | "total" | "prediction"
    volume_24h:             Optional[float] = None
    close_time:             Optional[datetime] = None
    url:                    Optional[str] = None
    raw:                    dict = Field(default_factory=dict)


class PlatformFees(BaseModel):
    profit_fee_pct:         float = 0.0
    trade_fee_pct:          float = 0.0
    withdrawal_fee_pct:     float = 0.0


class ArbLeg(BaseModel):
    contract:               MarketContract
    action:                 str          # "buy_yes", "buy_no", "sell_yes", "sell_no", etc.
    stake:                  float        # $ amount to wager / sell on this leg
    effective_cost:         float        # net cost after fees (negative = credit received for sells)
    expected_payout:        float        # what this leg returns if it wins
    platform_fees:          PlatformFees
    equivalent_buy_label:   Optional[str] = None  # For sell legs: the equivalent "buy X" label (e.g. sell OKC YES → buy DAL)


class ArbitrageOpportunity(BaseModel):
    id:                     str
    event_title:            str
    leg_yes:                ArbLeg       # First leg (buy side)
    leg_no:                 ArbLeg       # Second leg (complementary / sell side for spread arbs)
    total_cost:             float        # net capital at risk
    guaranteed_return:      float        # guaranteed profit/payout
    net_profit:             float        # guaranteed_return - total_cost
    net_profit_pct:         float        # net_profit / total_cost * 100
    expected_value:         float        # guaranteed_return / total_cost
    match_score:            float        # fuzzy match confidence 0.0–1.0
    arb_type:               str          # "cross_platform" | "sportsbook" | "spread"
    detected_at:            datetime
    expires_at:             Optional[datetime] = None


class NearCertaintyMarket(BaseModel):
    id:             str
    platform:       Platform
    event_title:    str
    outcome_label:  str          # The near-certain outcome (e.g. "YES", "DEN", "Republican")
    price:          float        # Contract price 0–1 (e.g. 0.98 = 98¢)
    implied_prob:   float        # price × 100  (e.g. 98.0)
    close_time:     Optional[Union[str, datetime]] = None   # ISO string or datetime
    url:            Optional[str] = None
    volume_24h:     Optional[float] = None
    detected_at:    datetime


class ScannerStatus(BaseModel):
    platform:               Platform
    last_scanned_at:        Optional[datetime] = None
    markets_found:          int = 0
    error:                  Optional[str] = None
    is_healthy:             bool = True


class OpportunitiesResponse(BaseModel):
    opportunities:          list[ArbitrageOpportunity]
    scanner_status:         list[ScannerStatus]
    scanned_at:             datetime
    total_markets:          int


class EvEdgeOpportunity(BaseModel):
    """
    A cross-platform opportunity (SB+PM or SB+SB) with asymmetric stakes that
    achieves positive probability-weighted EV while keeping the worst-case loss
    < 10% of total outlay.

    Primary metric — weighted_ev_pct:
      Uses each market's own implied probability as the "true" probability for
      that outcome:
        prob_leg_a = 1 / D_a         (SB leg A decimal odds)
        prob_leg_b = 1 / D_b  (SB)  or  pm price  (Kalshi/PM)
        weighted_ev_pct = (1 − prob_leg_a − prob_leg_b) × 100

      This is constant w.r.t. stake split; positive only when the combined
      implied probability < 100% (i.e. a genuine arb edge exists).

    Stake allocation is OPTIMISED for risk management:
      Finds f (fraction to leg A) at two constraint boundaries
      (each leg at exactly the 20% max-loss limit) and picks the boundary
      with the higher simple-average return.

    Displays only when:
      • weighted_ev_pct > 0         (positive probability-weighted EV)
      • max_loss_pct < 10           (worst-case loss < 10% of outlay)

    Supports SB+PM (Kalshi) and SB+SB pairs.
    """
    id:                     str
    event_title:            str
    sb_leg:                 ArbLeg       # Leg A — sportsbook side
    pm_leg:                 ArbLeg       # Leg B — Kalshi/PM or second sportsbook
    sb_stake:               float        # Optimal stake on leg A
    pm_stake:               float        # Optimal stake on leg B
    total_cost:             float        # sb_stake + pm_stake
    payout_if_sb_wins:      float        # Total return if leg A outcome wins
    payout_if_pm_wins:      float        # Total return if leg B outcome wins
    avg_return:             float        # (payout_a + payout_b) / 2
    avg_return_pct:         float        # (avg_return − total_cost) / total_cost × 100
    max_loss_pct:           float        # Actual max loss on the losing side as %
    ev_kalshi_pct:          float        # Kalshi-oracle EV% (PM price as true prob)
    delta_pct:              float        # |payout_a − payout_b| / total_cost × 100
    sb_fraction:            float        # Fraction of total on leg A (0–1)
    # ── Probability-weighted EV fields ──────────────────────────────────────
    weighted_ev_pct:        float        # Primary metric: (1 − prob_a − prob_b) × 100
    prob_leg_a:             float        # Implied prob of leg A outcome (0–1)
    prob_leg_b:             float        # Implied prob of leg B outcome (0–1)
    # ────────────────────────────────────────────────────────────────────────
    match_score:            float
    arb_type:               str = "ev_edge"
    detected_at:            datetime
    expires_at:             Optional[datetime] = None


class EvEdgesResponse(BaseModel):
    ev_edges:               list[EvEdgeOpportunity]
    scanner_status:         list[ScannerStatus]
    scanned_at:             datetime
    total_markets:          int


class ValueOpportunity(BaseModel):
    """
    A single-leg sportsbook bet that is mispriced relative to Pinnacle's oracle
    probability for the same outcome.

    How it works:
      • Pinnacle (the sharpest sportsbook) is used as the probability oracle.
        For each SB+Pinnacle hedge pair, the Pinnacle implied probability for
        the SAME outcome as the SB bet is computed as 1 / pinnacle_decimal_odds.
      • A hedge pair is: SB on outcome A  +  Pinnacle on outcome B (complement).
        The Pinnacle price for outcome A is the oracle probability for the SB bet.
      • Cross-market EV: cross_ev_pct = (oracle_prob × D_a − 1) × 100
      • Positive when Pinnacle implies higher probability than the SB's odds,
        i.e. the SB line is "soft" relative to the sharp Pinnacle line.

    This is a SINGLE-LEG bet signal pointing to a mispriced sportsbook line.
    No second leg is required; Pinnacle is used only as the probability reference.
    """
    id:              str
    event_title:     str
    sb_leg:          ArbLeg       # The mispriced sportsbook bet
    oracle_prob:     float        # Pinnacle implied probability for same outcome as SB bet
    oracle_platform: Platform     # Platform used as the probability oracle (Pinnacle)
    sb_implied_prob: float        # SB's own implied probability (1 / decimal_odds)
    cross_ev_pct:    float        # (oracle_prob × D_a − 1) × 100
    edge_ppts:       float        # (oracle_prob − sb_implied_prob) × 100 (prob edge, pp)
    match_score:     float
    arb_type:        str = "value"
    detected_at:     datetime
    expires_at:      Optional[datetime] = None


class ValueResponse(BaseModel):
    value_ops:      list[ValueOpportunity]
    scanner_status: list[ScannerStatus]
    scanned_at:     datetime
    total_markets:  int


class CryptoMarket(BaseModel):
    """
    A crypto prediction market from Kalshi or Polymarket.

    `is_arb` is True when yes_ask + no_ask < 1.00 — a guaranteed profit is
    available by buying both sides.  The net_profit_pct is realised regardless
    of whether the price goes up or down.

    Examples: KXBTC15M (BTC 15-minute direction), KXBTCD (BTC daily threshold),
    Polymarket BTC/ETH daily threshold markets.
    """
    platform:       str          # "kalshi" | "polymarket"
    event_ticker:   str
    market_ticker:  str
    asset:          str          # "BTC", "ETH", "BCH"
    market_type:    str          # "15m" | "daily" | "weekly"
    title:          str
    close_time:     datetime
    floor_strike:   float        # Reference price at window open
    yes_ask:        float        # Cost of YES contract (0–1)
    no_ask:         float        # Cost of NO contract (0–1)
    total_cost:     float        # yes_ask + no_ask
    is_arb:         bool         # True when total_cost < 1.00
    net_profit_pct: float        # (1 - total_cost) / total_cost * 100 (0 if no arb)
    url:            str


class CryptoScanResult(BaseModel):
    markets:    list[CryptoMarket]
    arb_count:  int
    scanned_at: datetime


class WebSocketMessage(BaseModel):
    type:                   str
    payload:                dict
