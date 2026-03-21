"""
Core arbitrage profit and stake calculations.

────────────────────────────────────────────────────────────────────────────────
ARB TYPE A: HEDGE ARB — SB + Kalshi  (cross-platform buy-buy)
────────────────────────────────────────────────────────────────────────────────
Buy Team A on DraftKings / FanDuel / Caesars + Buy Team B (opposite) on Kalshi.

  Variables:
    S   = SB base stake ($10 default)
    D   = Sportsbook decimal odds for Team A (e.g. 2.40 for +140)
    N   = S×D Kalshi shares of Team B  (e.g. $10 × 2.4 = 24 shares)
    P_B = Kalshi raw price for Team B  (e.g. $0.58 per share)

  P&L by outcome:
    • Team A wins:  SB pays S×D, Kalshi Team-B contract worthless → receive S×D
    • Team B wins:  SB stake S lost, Kalshi pays N×$1.00 = S×D       → receive S×D

  ✓ Delta-neutral. Arb condition: D×(1−P_B) > 1

────────────────────────────────────────────────────────────────────────────────
ARB TYPE B: HEDGE ARB — Kalshi only  (intra-PM)
────────────────────────────────────────────────────────────────────────────────
Buy YES + Buy NO on the same Kalshi binary event.
Arb when cost_yes + cost_no < $1.00 payout.

────────────────────────────────────────────────────────────────────────────────
ARB TYPE C: SPORTSBOOK ARB — SB + SB  ("surebet", cross-book)
────────────────────────────────────────────────────────────────────────────────
Buy Team A at Sportsbook A + Buy Team B at Sportsbook B.

  Payout convention for ALL supported sportsbooks (DK, FD, Caesars):
    total_return = stake × decimal_odds  (STAKE IS INCLUDED IN RETURN)
    +120 American = 2.20 decimal: $10 stake → $22 total return ($12 profit).

  Delta-neutral sizing (guarantee equal return G regardless of winner):
    Stake S_A = G / D_A  on Team A at Book A
    Stake S_B = G / D_B  on Team B at Book B
    total_cost = G × (1/D_A + 1/D_B)

  Arb condition: 1/D_A + 1/D_B < 1  (sum of implied probabilities < 100%)
  Profit = G × (1 − 1/D_A − 1/D_B)
  ROI%   = profit / total_cost × 100

  Normalized to G = SPREAD_BASE_STAKE ($10 guaranteed) so stakes are on the
  same scale as Type A arbs. Use rescale_opportunity() to resize positions.
"""
from __future__ import annotations
import copy
import hashlib
from datetime import datetime, timezone
from typing import Optional

import logging

from models import (
    MarketContract, ArbLeg, ArbitrageOpportunity, EvEdgeOpportunity,
    ValueOpportunity, PlatformFees, Platform, ContractSide,
)
from config import PLATFORM_FEES

logger = logging.getLogger(__name__)

# Active platforms — must mirror matcher.SPORTSBOOK_PLATFORMS exactly
SPORTSBOOK_PLATFORMS = {Platform.DRAFTKINGS, Platform.FANDUEL, Platform.CAESARS, Platform.PINNACLE}
PREDICTION_MARKET_PLATFORMS = {Platform.KALSHI, Platform.POLYMARKET}

# Bettable sportsbooks only (excludes Pinnacle, which is oracle-only for US users)
_BETTABLE_SB_PLATFORMS = {Platform.DRAFTKINGS, Platform.FANDUEL, Platform.CAESARS}

# Prediction-market platforms that can appear in the Value tab as a bet leg.
# Price IS the implied probability (0–1 per share) — no decimal_odds required.
_PM_PLATFORMS = {Platform.KALSHI, Platform.POLYMARKET}

# Base sportsbook stake for DK+Kalshi hedge — whole dollar for clean display.
# Kalshi shares: N = S × D (equal-profit sizing).
SPREAD_BASE_STAKE: float = 10.0

# Equal stake per leg for EV-edge display ($10 SB + $10 Kalshi = $20 total).
EV_EDGE_BASE_STAKE: float = 10.0


# ── Fee helpers ────────────────────────────────────────────────────────────────

def effective_buy_cost(price: float, payout: float, fees: PlatformFees) -> float:
    """True cost to BUY one contract share after fees."""
    cost = price
    if fees.trade_fee_pct > 0:
        cost = price * (1.0 + fees.trade_fee_pct)
    if fees.profit_fee_pct > 0:
        profit = payout - cost
        if profit > 0:
            cost += profit * fees.profit_fee_pct
    return round(cost, 6)


def effective_sell_proceeds(price: float, fees: PlatformFees) -> float:
    """
    Net credit received per share when SELLING a YES contract at 'price'.
    Kalshi's profit_fee applies when the contract resolves (buyer wins), not at sell time.
    So sell proceeds = price (no fee deducted upfront).
    """
    proceeds = price
    if fees.trade_fee_pct > 0:
        proceeds = price * (1.0 - fees.trade_fee_pct)
    return round(proceeds, 6)


def effective_payout(payout: float, fees: PlatformFees) -> float:
    """Net payout after withdrawal fees."""
    return round(payout * (1.0 - fees.withdrawal_fee_pct), 6)


def compute_optimal_stakes(dec_a: float, dec_b: float, target: float = 100.0):
    stake_a = target / dec_a
    stake_b = target / dec_b
    return round(stake_a, 4), round(stake_b, 4), round(target, 4)


def _opp_id(*parts: str) -> str:
    return hashlib.md5("|".join(parts).encode()).hexdigest()[:12]


def _action(c: MarketContract, is_sell: bool = False) -> str:
    verb = "sell" if is_sell else "buy"
    lbl = (c.outcome_label or "").lower().replace(" ", "_")
    if lbl in ("yes", "no", ""):
        direction = "yes" if c.is_yes_side else "no"
        return f"{verb}_{direction}"
    return f"{verb}_{lbl}"


# ── Hedge arb builder ──────────────────────────────────────────────────────────

def build_hedge_opportunity(
    contract_a: MarketContract,
    contract_b: MarketContract,
    match_score: float,
    event_title: str,
) -> Optional[ArbitrageOpportunity]:
    """
    Evaluate a cross-platform hedge arb: buy both sides of the same binary event.
    Exactly one leg pays out; combined cost must be < guaranteed payout.

    Valid structures with Kalshi + DraftKings:
      • Kalshi YES  + Kalshi NO  (intra-Kalshi, different-price binary)
      • Kalshi YES  + DraftKings away  (PM + SB, complementary buy-buy)
      • Kalshi NO   + DraftKings home  (PM + SB, complementary buy-buy)
    """
    fees_a = PLATFORM_FEES.get(contract_a.platform, PlatformFees())
    fees_b = PLATFORM_FEES.get(contract_b.platform, PlatformFees())
    is_sb_a = contract_a.platform in SPORTSBOOK_PLATFORMS
    is_sb_b = contract_b.platform in SPORTSBOOK_PLATFORMS

    # ── Mixed: one DraftKings + one Kalshi — buy-buy ─────────────────────────
    if is_sb_a or is_sb_b:
        sb_c, pm_c = (contract_a, contract_b) if is_sb_a else (contract_b, contract_a)
        sb_fees, pm_fees = (fees_a, fees_b) if is_sb_a else (fees_b, fees_a)
        if sb_c.decimal_odds is None:
            return None

        # DK: stake $S on Team A at decimal odds D.
        # Kalshi: buy N = S×D/payout shares of Team B (opposite outcome) at raw price P_B.
        # Guaranteed return = N × payout = S × D regardless of which team wins.
        S = SPREAD_BASE_STAKE
        D = sb_c.decimal_odds
        N = round(S * D / pm_c.payout_per_contract, 2)

        pm_raw_cost = N * pm_c.price          # dollars paid to buy N Kalshi shares
        total_cost  = S + pm_raw_cost          # DK stake + Kalshi buy cost
        guaranteed  = N * pm_c.payout_per_contract  # = S × D

        if total_cost >= guaranteed:
            return None

        if is_sb_a:
            stake_a, stake_b = S,            pm_raw_cost
            eff_a,   eff_b   = S,            pm_raw_cost
        else:
            stake_a, stake_b = pm_raw_cost,  S
            eff_a,   eff_b   = pm_raw_cost,  S
        arb_type = "cross_platform"

    # ── Both Kalshi (intra-PM hedge) ─────────────────────────────────────────
    else:
        payout_a = effective_payout(contract_a.payout_per_contract, fees_a)
        payout_b = effective_payout(contract_b.payout_per_contract, fees_b)
        guaranteed = min(payout_a, payout_b)
        eff_a = effective_buy_cost(contract_a.price, contract_a.payout_per_contract, fees_a)
        eff_b = effective_buy_cost(contract_b.price, contract_b.payout_per_contract, fees_b)
        total_cost = eff_a + eff_b
        stake_a = contract_a.price
        stake_b = contract_b.price
        if total_cost >= guaranteed:
            return None
        arb_type = "cross_platform"

    net_profit = round(guaranteed - total_cost, 6)
    if net_profit < 0.001:
        return None

    net_profit_pct = round((net_profit / total_cost) * 100, 4)
    ev = round(guaranteed / total_cost, 6)

    return ArbitrageOpportunity(
        id=_opp_id(contract_a.platform, contract_a.market_id, contract_a.side,
                   contract_b.platform, contract_b.market_id, contract_b.side),
        event_title=event_title,
        leg_yes=ArbLeg(
            contract=contract_a, action=_action(contract_a),
            stake=round(stake_a, 6), effective_cost=round(eff_a, 6),
            expected_payout=round(guaranteed, 6), platform_fees=fees_a,
        ),
        leg_no=ArbLeg(
            contract=contract_b, action=_action(contract_b),
            stake=round(stake_b, 6), effective_cost=round(eff_b, 6),
            expected_payout=round(guaranteed, 6), platform_fees=fees_b,
        ),
        total_cost=round(total_cost, 6),
        guaranteed_return=round(guaranteed, 6),
        net_profit=net_profit,
        net_profit_pct=net_profit_pct,
        expected_value=ev,
        match_score=match_score,
        arb_type=arb_type,
        detected_at=datetime.now(timezone.utc),
    )


# Backwards-compat alias
build_opportunity = build_hedge_opportunity


# ── Spread arb builder ─────────────────────────────────────────────────────────

def build_spread_opportunity(
    contract_a: MarketContract,
    contract_b: MarketContract,
    match_score: float,
    event_title: str,
) -> Optional[ArbitrageOpportunity]:
    """
    Evaluate a same-side spread arb: buy where cheap, sell where expensive.

    DraftKings + Kalshi spread arbs are now handled as buy-buy hedge arbs
    in build_hedge_opportunity. This function only handles intra-Kalshi spreads.
    """
    fees_a = PLATFORM_FEES.get(contract_a.platform, PlatformFees())
    fees_b = PLATFORM_FEES.get(contract_b.platform, PlatformFees())
    is_sb_a = contract_a.platform in SPORTSBOOK_PLATFORMS
    is_sb_b = contract_b.platform in SPORTSBOOK_PLATFORMS

    # ── DraftKings + Kalshi: now handled by build_hedge_opportunity ───────────
    if is_sb_a or is_sb_b:
        return None

    # ── Kalshi vs Kalshi (intra-PM spread — same outcome different prices) ────
    buy_a_cost  = effective_buy_cost(contract_a.price, contract_a.payout_per_contract, fees_a)
    sell_b_proc = effective_sell_proceeds(contract_b.price, fees_b)
    buy_b_cost  = effective_buy_cost(contract_b.price, contract_b.payout_per_contract, fees_b)
    sell_a_proc = effective_sell_proceeds(contract_a.price, fees_a)

    payout_a = effective_payout(contract_a.payout_per_contract, fees_a)
    payout_b = effective_payout(contract_b.payout_per_contract, fees_b)
    guaranteed = min(payout_a, payout_b)

    collateral_b = contract_b.payout_per_contract - contract_b.price
    net_cost_ab  = buy_a_cost + collateral_b
    profit_ab    = guaranteed - net_cost_ab

    collateral_a = contract_a.payout_per_contract - contract_a.price
    net_cost_ba  = buy_b_cost + collateral_a
    profit_ba    = guaranteed - net_cost_ba

    if profit_ab >= profit_ba:
        buy_c, sell_c = contract_a, contract_b
        buy_fees, sell_fees = fees_a, fees_b
        net_cost = net_cost_ab
        profit = profit_ab
        buy_stake = contract_a.price
        sell_collateral = collateral_b
        buy_eff = buy_a_cost
    else:
        buy_c, sell_c = contract_b, contract_a
        buy_fees, sell_fees = fees_b, fees_a
        net_cost = net_cost_ba
        profit = profit_ba
        buy_stake = contract_b.price
        sell_collateral = collateral_a
        buy_eff = buy_b_cost

    if profit < 0.0001:
        return None

    net_profit_pct = round((profit / net_cost) * 100, 4)
    ev = round(guaranteed / net_cost, 6)

    return ArbitrageOpportunity(
        id=_opp_id("spread", buy_c.platform.value, buy_c.market_id,
                   sell_c.platform.value, sell_c.market_id),
        event_title=event_title,
        leg_yes=ArbLeg(
            contract=buy_c,
            action=_action(buy_c, is_sell=False),
            stake=round(buy_stake, 6),
            effective_cost=round(buy_eff, 6),
            expected_payout=round(guaranteed, 6),
            platform_fees=buy_fees,
        ),
        leg_no=ArbLeg(
            contract=sell_c,
            action=_action(sell_c, is_sell=True),
            stake=1.0,
            effective_cost=round(sell_collateral, 6),
            expected_payout=round(guaranteed, 6),
            platform_fees=sell_fees,
        ),
        total_cost=round(net_cost, 6),
        guaranteed_return=round(guaranteed, 6),
        net_profit=round(profit, 6),
        net_profit_pct=net_profit_pct,
        expected_value=ev,
        match_score=match_score,
        arb_type="spread",
        detected_at=datetime.now(timezone.utc),
    )


# ── Cross-sportsbook arb builder ──────────────────────────────────────────────

def build_sportsbook_arb(
    contract_a: MarketContract,
    contract_b: MarketContract,
    match_score: float,
    event_title: str,
) -> Optional[ArbitrageOpportunity]:
    """
    Evaluate a cross-sportsbook ("surebet") arb: buy opposite outcomes at two
    different sportsbooks.

    Payout convention for all supported books (DK, FD, Caesars):
      total_return = stake × decimal_odds  (stake IS included in return)
      +120 American = 2.20 decimal → $10 stake → $22 back (incl. $10 stake).

    Delta-neutral sizing:
      Set guaranteed return G = SPREAD_BASE_STAKE.
      Stake S_A = G / D_A on the first book, S_B = G / D_B on the second.
      total_cost = S_A + S_B = G × (1/D_A + 1/D_B)
      Arb iff 1/D_A + 1/D_B < 1 (combined implied probability < 100%).

    Returns None if:
      • Either leg lacks decimal_odds (non-sportsbook contract)
      • Either platform is not a sportsbook
      • Combined implied probability ≥ 100% (no edge after vig)
    """
    if contract_a.decimal_odds is None or contract_b.decimal_odds is None:
        return None
    if (contract_a.platform not in SPORTSBOOK_PLATFORMS or
            contract_b.platform not in SPORTSBOOK_PLATFORMS):
        return None
    # Pinnacle is oracle-only — not a bettable leg in cross-book arbs
    if (contract_a.platform == Platform.PINNACLE or
            contract_b.platform == Platform.PINNACLE):
        return None
    # Same sportsbook: users can't bet both sides of a game at the same book
    if contract_a.platform == contract_b.platform:
        return None

    D_A = contract_a.decimal_odds
    D_B = contract_b.decimal_odds

    # Sum of implied probabilities — arb exists iff < 1.0
    sum_implied = 1.0 / D_A + 1.0 / D_B
    if sum_implied >= 1.0:
        return None

    # Delta-neutral stakes: each leg guarantees the same return G
    G = SPREAD_BASE_STAKE   # target guaranteed return ($10)
    S_A = G / D_A
    S_B = G / D_B
    total_cost = S_A + S_B  # = G × sum_implied

    net_profit = G - total_cost
    if net_profit < 0.001:
        return None

    net_profit_pct = round((net_profit / total_cost) * 100, 4)
    ev = round(G / total_cost, 6)

    # Sportsbooks have no explicit per-trade fees we model (vig is priced in)
    fees_zero = PlatformFees()

    return ArbitrageOpportunity(
        id=_opp_id("sb2", contract_a.platform.value, contract_a.market_id,
                   contract_b.platform.value, contract_b.market_id),
        event_title=event_title,
        leg_yes=ArbLeg(
            contract=contract_a, action=_action(contract_a),
            stake=round(S_A, 6), effective_cost=round(S_A, 6),
            expected_payout=round(G, 6), platform_fees=fees_zero,
        ),
        leg_no=ArbLeg(
            contract=contract_b, action=_action(contract_b),
            stake=round(S_B, 6), effective_cost=round(S_B, 6),
            expected_payout=round(G, 6), platform_fees=fees_zero,
        ),
        total_cost=round(total_cost, 6),
        guaranteed_return=round(G, 6),
        net_profit=round(net_profit, 6),
        net_profit_pct=net_profit_pct,
        expected_value=ev,
        match_score=match_score,
        arb_type="sportsbook",
        detected_at=datetime.now(timezone.utc),
    )


# ── Outlay rescaler ────────────────────────────────────────────────────────────

def rescale_opportunity(
    opp: ArbitrageOpportunity,
    target_outlay: float,
) -> ArbitrageOpportunity:
    """
    Proportionally rescale both legs to hit a desired total outlay.

    All monetary values scale linearly: stakes, effective costs, payouts, net profit.
    Ratio metrics (net_profit_pct, expected_value, match_score) are unchanged since
    they are dimensionless — the arb's edge does not depend on position size.

    Example:
      Base arb:   total_cost=$23.92, guaranteed=$24.00, net_profit=$0.08
      target=100: scale=4.18x  → total_cost=$100.00, guaranteed=$100.33, profit=$0.33
    """
    if opp.total_cost <= 0 or target_outlay <= 0:
        return opp

    scale = target_outlay / opp.total_cost

    def _scale_leg(leg: ArbLeg) -> ArbLeg:
        return ArbLeg(
            contract=leg.contract,
            action=leg.action,
            stake=round(leg.stake * scale, 6),
            effective_cost=round(leg.effective_cost * scale, 6),
            expected_payout=round(leg.expected_payout * scale, 6),
            platform_fees=leg.platform_fees,
        )

    return ArbitrageOpportunity(
        id=opp.id,
        event_title=opp.event_title,
        leg_yes=_scale_leg(opp.leg_yes),
        leg_no=_scale_leg(opp.leg_no),
        total_cost=round(target_outlay, 6),
        guaranteed_return=round(opp.guaranteed_return * scale, 6),
        net_profit=round(opp.net_profit * scale, 6),
        net_profit_pct=opp.net_profit_pct,   # ratio — unchanged
        expected_value=opp.expected_value,    # ratio — unchanged
        match_score=opp.match_score,
        arb_type=opp.arb_type,
        detected_at=opp.detected_at,
        expires_at=opp.expires_at,
    )


# ── EV-edge builder ────────────────────────────────────────────────────────────

# Stake optimisation boundary AND display filter: max loss ≤ 10% on each leg.
# (Setting this to 0.10 means MIN_RETURN = 0.90 — both legs must return ≥ 90%.)
EV_EDGE_MAX_LOSS_FRACTION: float = 0.10
EV_EDGE_BASE_TOTAL: float        = 20.0  # Reference total outlay for display ($)


def build_ev_edge(
    contract_a: MarketContract,
    contract_b: MarketContract,
    match_score: float,
    event_title: str,
    *,
    oracle_prob: Optional[float] = None,
) -> Optional[EvEdgeOpportunity]:
    """
    Find the optimal unequal stake split for a SB+PM or SB+SB pair using
    probability-weighted EV as the primary filter and metric.

    Probability-weighted EV formula (per dollar of total outlay, constant in f):
      prob_a = 1 / D_a           (SB leg A implied probability)
      prob_b = 1 / D_b  (SB+SB)  or  pm_c.price  (SB+PM/Kalshi)
      weighted_ev_frac = 1 − prob_a − prob_b

    EV > 0  iff  prob_a + prob_b < 1  (equivalent to the arb condition).
    Note: the guaranteed-arb exclusion is intentionally removed here so that
    any pair with positive probability-weighted EV can surface in this tab
    (the arb tab continues to show guaranteed arbs via build_hedge_opportunity).

    Stake split f is optimised for risk management (minimise max loss ≤ 10%),
    evaluated at two constraint boundaries:
      Boundary 1: leg B at exactly max-loss limit
      Boundary 2: leg A at exactly max-loss limit
    The boundary with the higher average return is chosen.

    Supports:
      SB + PM (Kalshi): leg B payout per unit = (1−f) / P_K
      SB + SB         : leg B payout per unit = (1−f) × D_b

    Filters applied (returns None if any fail):
      • avg_return_frac > 1.0  (50/50 average payout exceeds total outlay)
      • max_loss_pct ≤ EV_EDGE_MAX_LOSS_FRACTION × 100  (worst-case loss ≤ 10%)
    """
    # Pinnacle is oracle-only — never a bettable leg in EV edge opportunities
    if (contract_a.platform == Platform.PINNACLE or
            contract_b.platform == Platform.PINNACLE):
        return None

    is_sb_a = contract_a.platform in SPORTSBOOK_PLATFORMS
    is_sb_b = contract_b.platform in SPORTSBOOK_PLATFORMS

    # Need at least one SB leg; PM+PM not applicable here
    if not is_sb_a and not is_sb_b:
        return None

    # Can't bet both sides at the same sportsbook
    if is_sb_a and is_sb_b and contract_a.platform == contract_b.platform:
        return None

    # Leg A is always the sportsbook (or contract_a when both are SB)
    leg_a_c, leg_b_c = (contract_a, contract_b) if is_sb_a else (contract_b, contract_a)

    if leg_a_c.decimal_odds is None:
        return None

    D_a = leg_a_c.decimal_odds
    prob_a = 1.0 / D_a

    is_leg_b_sb = leg_b_c.platform in SPORTSBOOK_PLATFORMS

    if is_leg_b_sb:
        # SB + SB: both legs use decimal odds
        if leg_b_c.decimal_odds is None:
            return None
        D_b: Optional[float] = leg_b_c.decimal_odds
        prob_b = 1.0 / D_b
        P_K: Optional[float] = None
    else:
        # SB + PM: leg B price IS its implied probability
        P_K = leg_b_c.price
        if P_K <= 0.0 or P_K >= 1.0:
            return None
        D_b = None
        prob_b = P_K

    # ── Probability-weighted EV (informational — NOT used as filter) ─────────
    # EV = 1 − prob_a − prob_b  is equivalent to the guaranteed-arb condition.
    # For any market with real vig, prob_a + prob_b > 1, so this is always < 0
    # for non-guaranteed arbs.  We compute it for display but do NOT gate on it.
    weighted_ev_frac = 1.0 - prob_a - prob_b   # displayed as weighted_ev_pct

    # ── Stake optimisation: evaluate two constraint boundaries ────────────────
    # MIN_RETURN = 0.90 → each leg returns ≥ 90% of total outlay (≤ 10% loss)
    MIN_RETURN = 1.0 - EV_EDGE_MAX_LOSS_FRACTION  # 0.90

    # Each candidate: (avg_return_frac, f, payout_a_frac, payout_b_frac)
    candidates: list[tuple[float, float, float, float]] = []

    if D_b is not None:
        # SB + SB: payout_b(f) = (1−f) × D_b
        # Boundary 1: leg B at max-loss  →  (1−f)×D_b = MIN_RETURN
        f1 = 1.0 - MIN_RETURN / D_b
        if 0.0 < f1 < 1.0:
            pa1 = f1 * D_a
            pb1 = MIN_RETURN
            if pa1 >= MIN_RETURN:
                avg1 = (pa1 + pb1) / 2.0
                if avg1 > 1.0:
                    candidates.append((avg1, f1, pa1, pb1))

        # Boundary 2: leg A at max-loss  →  f×D_a = MIN_RETURN
        f2 = MIN_RETURN / D_a
        if 0.0 < f2 < 1.0:
            pa2 = MIN_RETURN
            pb2 = (1.0 - f2) * D_b
            if pb2 >= MIN_RETURN:
                avg2 = (pa2 + pb2) / 2.0
                if avg2 > 1.0:
                    candidates.append((avg2, f2, pa2, pb2))
    else:
        # SB + PM: payout_b(f) = (1−f) / P_K
        # Boundary 1: PM leg at max-loss  →  (1−f)/P_K = MIN_RETURN
        f1 = 1.0 - MIN_RETURN * P_K
        if 0.0 < f1 < 1.0:
            pa1 = f1 * D_a
            pb1 = MIN_RETURN
            if pa1 >= MIN_RETURN:
                avg1 = (pa1 + pb1) / 2.0
                if avg1 > 1.0:
                    candidates.append((avg1, f1, pa1, pb1))

        # Boundary 2: SB leg at max-loss  →  f×D_a = MIN_RETURN
        f2 = MIN_RETURN / D_a
        if 0.0 < f2 < 1.0:
            pa2 = MIN_RETURN
            pb2 = (1.0 - f2) / P_K
            if pb2 >= MIN_RETURN:
                avg2 = (pa2 + pb2) / 2.0
                if avg2 > 1.0:
                    candidates.append((avg2, f2, pa2, pb2))

    if not candidates:
        return None

    # Pick the allocation with the highest average return
    _, f, payout_a_frac, payout_b_frac = max(candidates, key=lambda c: c[0])

    # ── Scale to reference total outlay ──────────────────────────────────────
    T          = EV_EDGE_BASE_TOTAL
    stake_a    = round(f * T, 2)
    stake_b    = round((1.0 - f) * T, 2)
    total_cost = stake_a + stake_b  # ≈ T (small rounding difference)

    payout_if_a_wins = round(payout_a_frac * T, 2)
    payout_if_b_wins = round(payout_b_frac * T, 2)

    # ── Display max-loss filter (≤ 10%) ───────────────────────────────────────
    min_payout   = min(payout_if_a_wins, payout_if_b_wins)
    max_loss_pct = round((total_cost - min_payout) / total_cost * 100.0, 1)
    if max_loss_pct > EV_EDGE_MAX_LOSS_FRACTION * 100.0:
        return None  # Worst-case loss exceeds 10% threshold

    # ── Derived metrics ───────────────────────────────────────────────────────
    avg_return      = round((payout_if_a_wins + payout_if_b_wins) / 2.0, 2)
    avg_return_pct  = round(
        ((payout_if_a_wins + payout_if_b_wins) / 2.0 / total_cost - 1.0) * 100.0, 3
    )
    delta_pct = round(abs(payout_if_a_wins - payout_if_b_wins) / total_cost * 100.0, 1)

    # Kalshi-oracle EV (supplementary): uses Kalshi PM price as true probability
    if P_K is not None:
        p_a_true      = 1.0 - P_K
        ev_kalshi_raw = p_a_true * payout_if_a_wins + P_K * payout_if_b_wins - total_cost
        ev_kalshi_pct = round(ev_kalshi_raw / total_cost * 100.0, 3)
    else:
        ev_kalshi_pct = round(weighted_ev_frac * 100.0, 3)

    # ── Pinnacle oracle probability-weighted EV (primary metric) ──────────────
    # oracle_prob is Pinnacle's implied probability for leg A's outcome.
    # When available, it gives a genuine probability-weighted EV using the
    # sharpest public line as the "true probability" reference.
    # For a binary event: P(B) = 1 − P(A), so the two probs always sum to 1.
    if oracle_prob is not None and 0.0 < oracle_prob < 1.0:
        ev_prob_a = oracle_prob
        ev_prob_b = 1.0 - oracle_prob
        pin_ev = ev_prob_a * payout_if_a_wins + ev_prob_b * payout_if_b_wins - total_cost
        weighted_ev_pct = round(pin_ev / total_cost * 100.0, 3)
        # With a reliable probability oracle, only surface positive-EV positions
        if weighted_ev_pct <= 0.0:
            return None
    else:
        # No Pinnacle oracle: fall back to market-implied probabilities
        # (positive only when implied probs sum < 1 — equivalent to arb condition)
        ev_prob_a = prob_a
        ev_prob_b = prob_b
        weighted_ev_pct = round(weighted_ev_frac * 100.0, 3)

    # ── ArbLeg objects ────────────────────────────────────────────────────────
    fees_a = PLATFORM_FEES.get(leg_a_c.platform, PlatformFees())
    fees_b = PLATFORM_FEES.get(leg_b_c.platform, PlatformFees())

    action_a = f"buy_{(leg_a_c.outcome_label or 'a').lower().replace(' ', '_')}"
    action_b = f"buy_{(leg_b_c.outcome_label or 'b').lower().replace(' ', '_')}"

    sb_leg = ArbLeg(
        contract=leg_a_c,
        action=action_a,
        stake=stake_a,
        effective_cost=stake_a,
        expected_payout=payout_if_a_wins,
        platform_fees=fees_a,
    )
    pm_leg = ArbLeg(
        contract=leg_b_c,
        action=action_b,
        stake=stake_b,
        effective_cost=stake_b,
        expected_payout=payout_if_b_wins,
        platform_fees=fees_b,
    )

    return EvEdgeOpportunity(
        id=_opp_id("ev", leg_a_c.platform.value, leg_a_c.market_id,
                   leg_b_c.platform.value, leg_b_c.market_id),
        event_title=event_title,
        sb_leg=sb_leg,
        pm_leg=pm_leg,
        sb_stake=stake_a,
        pm_stake=stake_b,
        total_cost=total_cost,
        payout_if_sb_wins=payout_if_a_wins,
        payout_if_pm_wins=payout_if_b_wins,
        avg_return=avg_return,
        avg_return_pct=avg_return_pct,
        max_loss_pct=max_loss_pct,
        ev_kalshi_pct=ev_kalshi_pct,
        delta_pct=delta_pct,
        sb_fraction=round(f, 4),
        weighted_ev_pct=weighted_ev_pct,
        prob_leg_a=round(ev_prob_a, 4),
        prob_leg_b=round(ev_prob_b, 4),
        match_score=match_score,
        detected_at=datetime.now(timezone.utc),
    )


# ── Cross-market value builder ──────────────────────────────────────────────────

# Minimum cross-market edge to surface a value opportunity.
# cross_ev_pct = (oracle_prob × D_a − 1) × 100 must exceed this.
EV_VALUE_MIN_CROSS_EV_PCT: float = 1.0    # 1% minimum Pinnacle-oracle EV on the SB bet
EV_VALUE_MIN_EDGE_PPTS:    float = 1.5    # 1.5 percentage-point gap between Pinnacle and SB probs
EV_VALUE_BASE_STAKE:       float = 10.0   # Reference stake for display ($10)


def build_value_opportunity(
    sb_contract: MarketContract,
    oracle_price: float,
    match_score: float,
    event_title: str,
    *,
    oracle_platform: Platform,
) -> Optional[ValueOpportunity]:
    """
    Identify a single-leg bet (sportsbook OR Kalshi/PM) that is mispriced
    relative to the sharp-line oracle (Pinnacle).

    oracle_price is the oracle's implied probability for the SAME outcome as
    the bet (1 / pinnacle_decimal_odds for SB; compared against Kalshi price
    directly for PM since Kalshi price IS the implied probability).

    Handles both bet types:
      • Sportsbook (DK/FD/Caesars): uses decimal_odds to compute D_a and sb_implied.
      • Prediction market (Kalshi): price IS the implied probability;
        effective decimal odds D_a = 1 / price (payout per dollar staked).

    cross_ev_pct = (oracle_prob × D_a − 1) × 100
      For SB:    D_a = decimal_odds
      For Kalshi: D_a = 1 / price  (same formula, identical interpretation)

    Filters (returns None if any fail):
      • bet platform must be bettable SB or Kalshi
      • bet platform must differ from oracle_platform (no self-comparison)
      • oracle_price must be in (0, 1)
      • cross_ev_pct > EV_VALUE_MIN_CROSS_EV_PCT  (oracle EV > 1%)
      • edge_ppts    ≥ EV_VALUE_MIN_EDGE_PPTS      (prob gap ≥ 1.5 pp)
    """
    is_pm = sb_contract.platform in _PM_PLATFORMS
    is_sb = sb_contract.platform in _BETTABLE_SB_PLATFORMS

    if not is_pm and not is_sb:
        return None
    if sb_contract.platform == oracle_platform:
        return None   # Never compare oracle against itself
    if not (0.0 < oracle_price < 1.0):
        return None

    if is_pm:
        # Kalshi/PM: the contract price IS the implied probability (0–1 per share).
        # Effective decimal odds = 1/price (a $10 stake buys 10/price shares
        # paying $1 each, so total payout = $10/price = $10 × D_a).
        price = sb_contract.price
        if price <= 0.0 or price >= 1.0:
            return None
        D_a       = 1.0 / price
        sb_implied = price
    else:
        # Sportsbook: requires decimal_odds
        if sb_contract.decimal_odds is None:
            return None
        D_a       = sb_contract.decimal_odds
        sb_implied = 1.0 / D_a

    cross_ev_pct = round((oracle_price * D_a - 1.0) * 100.0, 3)
    edge_ppts    = round((oracle_price - sb_implied) * 100.0, 3)

    platform_tag = "pm" if is_pm else "sb"

    # Determine filter outcome for diagnostic logging
    if cross_ev_pct <= EV_VALUE_MIN_CROSS_EV_PCT:
        logger.info(
            f"[value FILTERED-ev/{platform_tag}] {sb_contract.platform.value} {sb_contract.outcome_label!r} "
            f"implied={sb_implied*100:.1f}% oracle={oracle_price*100:.1f}% "
            f"edge={edge_ppts:+.1f}pp cross_ev={cross_ev_pct:+.2f}% "
            f"← cross_ev ≤ {EV_VALUE_MIN_CROSS_EV_PCT}% threshold"
        )
        return None
    if edge_ppts < EV_VALUE_MIN_EDGE_PPTS:
        logger.info(
            f"[value FILTERED-edge/{platform_tag}] {sb_contract.platform.value} {sb_contract.outcome_label!r} "
            f"implied={sb_implied*100:.1f}% oracle={oracle_price*100:.1f}% "
            f"edge={edge_ppts:+.1f}pp cross_ev={cross_ev_pct:+.2f}% "
            f"← edge_ppts < {EV_VALUE_MIN_EDGE_PPTS}pp threshold"
        )
        return None

    logger.info(
        f"[value PASSED/{platform_tag}] {sb_contract.platform.value} {sb_contract.outcome_label!r} "
        f"implied={sb_implied*100:.1f}% oracle({oracle_platform.value})={oracle_price*100:.1f}% "
        f"edge={edge_ppts:+.1f}pp cross_ev={cross_ev_pct:+.2f}%"
    )

    fees   = PLATFORM_FEES.get(sb_contract.platform, PlatformFees())
    action = f"buy_{(sb_contract.outcome_label or 'outcome').lower().replace(' ', '_')}"
    payout = round(EV_VALUE_BASE_STAKE * D_a, 2)

    bet_leg = ArbLeg(
        contract=sb_contract,
        action=action,
        stake=EV_VALUE_BASE_STAKE,
        effective_cost=EV_VALUE_BASE_STAKE,
        expected_payout=payout,
        platform_fees=fees,
    )

    return ValueOpportunity(
        id=_opp_id("val", sb_contract.platform.value, sb_contract.market_id, str(sb_contract.is_yes_side)),
        event_title=event_title,
        sb_leg=bet_leg,
        oracle_prob=round(oracle_price, 4),
        oracle_platform=oracle_platform,
        sb_implied_prob=round(sb_implied, 4),
        cross_ev_pct=cross_ev_pct,
        edge_ppts=edge_ppts,
        match_score=match_score,
        detected_at=datetime.now(timezone.utc),
    )
