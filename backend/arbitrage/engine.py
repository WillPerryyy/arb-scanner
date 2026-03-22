"""
Arbitrage engine orchestrator — Kalshi + DraftKings + FanDuel + Caesars.

All arbs are buy-buy (both legs are BUY orders). Four types:

  1. SB+Kalshi hedge (arb_type="cross_platform"):
       Buy Team A at any sportsbook + Buy Team B (opposite) on Kalshi.
       S = SB stake, D = decimal odds, N = S*D Kalshi shares at price P_B.
       Arb iff S + N*P_B < S*D, i.e. D*(1-P_B) > 1.

  2. Intra-Kalshi hedge (arb_type="cross_platform"):
       Buy YES + Buy NO on the same Kalshi binary market.
       Arb when cost_yes + cost_no < $1.00 payout.

  3. Cross-sportsbook surebet (arb_type="sportsbook"):
       Buy Team A at Sportsbook A + Buy Team B at Sportsbook B.
       All supported books use decimal odds (stake included in return).
       S_A = G/D_A, S_B = G/D_B; arb iff 1/D_A + 1/D_B < 1.

  4. Intra-Kalshi spread (arb_type="spread"):
       Buy Kalshi + Sell Kalshi on same outcome at different prices.

Deduplication:
  SB+Kalshi: best sportsbook per Kalshi contract (highest ROI wins).
  SB+SB: sorted symmetric key prevents showing (DK:A + FD:B) and (FD:B + DK:A).
"""
from __future__ import annotations
import asyncio
import logging

import httpx

from scanners.kalshi      import KalshiScanner
from scanners.draftkings  import DraftKingsScanner
from scanners.fanduel     import FanDuelScanner
from scanners.caesars     import CaesarsScanner
from scanners.pinnacle    import PinnacleScanner
from scanners.odds_api    import OddsApiScanner
from scanners.polymarket  import PolymarketScanner
from arbitrage.matcher    import (
    find_matching_pairs,
    PREDICTION_MARKET_PLATFORMS,
    SPORTSBOOK_PLATFORMS,
)
from arbitrage.calculator import (
    build_hedge_opportunity, build_spread_opportunity, build_sportsbook_arb,
    build_ev_edge, build_value_opportunity,
)
from models import ArbitrageOpportunity, EvEdgeOpportunity, ValueOpportunity, ScannerStatus, Platform

logger = logging.getLogger(__name__)

ALL_SCANNER_CLASSES = [
    KalshiScanner,
    DraftKingsScanner,
    FanDuelScanner,
    CaesarsScanner,
    PinnacleScanner,    # Consensus oracle via Action Network (book_id=15, current market price); same abbr namespace as DK/Kalshi
    OddsApiScanner,     # BetMGM h2h + FanDuel/Caesars/BetMGM spreads & totals
    PolymarketScanner,  # Binary prediction markets (public API, no key needed)
]


def _best_sb_per_kalshi_contract(
    opportunities: list[ArbitrageOpportunity],
) -> list[ArbitrageOpportunity]:
    """
    For cross-platform hedge arbs (SB + Kalshi buy-buy), keep only the
    highest-ROI sportsbook per Kalshi contract.

    If DraftKings, FanDuel, and Caesars all offer an arb against the same
    Kalshi leg (same market_id + side), only the book with the best odds
    (highest net_profit_pct) is shown to the user.

    Intra-Kalshi hedge arbs (YES + NO same market) are passed through unchanged,
    keyed uniquely by both contract identifiers.
    """
    non_cross = [o for o in opportunities if o.arb_type != "cross_platform"]
    cross_opps = [o for o in opportunities if o.arb_type == "cross_platform"]

    best: dict[str, ArbitrageOpportunity] = {}

    for opp in cross_opps:
        ly_plat = opp.leg_yes.contract.platform
        ln_plat = opp.leg_no.contract.platform

        # SB (leg_yes) + Kalshi (leg_no)
        if ly_plat in SPORTSBOOK_PLATFORMS and ln_plat in PREDICTION_MARKET_PLATFORMS:
            pm_c = opp.leg_no.contract
            key = f"{pm_c.platform.value}:{pm_c.market_id}:{pm_c.side}"

        # Kalshi (leg_yes) + SB (leg_no)
        elif ly_plat in PREDICTION_MARKET_PLATFORMS and ln_plat in SPORTSBOOK_PLATFORMS:
            pm_c = opp.leg_yes.contract
            key = f"{pm_c.platform.value}:{pm_c.market_id}:{pm_c.side}"

        # Intra-Kalshi hedge (YES + NO) — keep all, unique key from both sides
        else:
            key = (
                f"intra|{opp.leg_yes.contract.market_id}:{opp.leg_yes.contract.side}"
                f"|{opp.leg_no.contract.market_id}:{opp.leg_no.contract.side}"
            )

        if key not in best or opp.net_profit_pct > best[key].net_profit_pct:
            best[key] = opp

    return non_cross + list(best.values())


def _best_spread_per_pm_contract(
    opportunities: list[ArbitrageOpportunity],
) -> list[ArbitrageOpportunity]:
    """
    For spread arbs involving a PM sell leg, deduplicate by PM contract:
    keep only the highest-ROI sportsbook per PM sell contract.
    PM vs PM spreads pass through unchanged.
    """
    non_spread = [o for o in opportunities if o.arb_type != "spread"]
    spread_opps = [o for o in opportunities if o.arb_type == "spread"]

    best: dict[str, ArbitrageOpportunity] = {}

    for opp in spread_opps:
        ly_platform = opp.leg_yes.contract.platform
        ln_platform = opp.leg_no.contract.platform

        if ly_platform in PREDICTION_MARKET_PLATFORMS:
            pm_contract = opp.leg_yes.contract
        elif ln_platform in PREDICTION_MARKET_PLATFORMS:
            pm_contract = opp.leg_no.contract
        else:
            key = f"pmpm|{opp.leg_yes.contract.market_id}|{opp.leg_no.contract.market_id}"
            if key not in best or opp.net_profit_pct > best[key].net_profit_pct:
                best[key] = opp
            continue

        key = f"{pm_contract.platform.value}:{pm_contract.market_id}"
        if key not in best or opp.net_profit_pct > best[key].net_profit_pct:
            best[key] = opp

    return non_spread + list(best.values())


async def run_full_scan() -> tuple[list[ArbitrageOpportunity], list[EvEdgeOpportunity], list[ValueOpportunity], list[ScannerStatus], dict[str, int]]:
    """
    1. Run Kalshi + DraftKings + FanDuel + Caesars scanners concurrently.
    2. Match events across platforms (exact key + fuzzy).
    3. Evaluate hedge arbs (buy-buy): SB+Kalshi and intra-Kalshi.
    4. Evaluate spread arbs (intra-Kalshi only).
    5. Deduplicate: best sportsbook per Kalshi contract, then sort by ROI%.
    """
    all_contracts = []
    statuses: list[ScannerStatus] = []

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        scanners = [cls(client) for cls in ALL_SCANNER_CLASSES]
        raw_results = await asyncio.gather(
            *[s.scan() for s in scanners],
            return_exceptions=True,
        )

    for result in raw_results:
        if isinstance(result, Exception):
            logger.error(f"Scanner raised unhandled exception: {result}")
            continue
        contracts, status = result
        all_contracts.extend(contracts)
        statuses.append(status)

    logger.info(
        f"Scan complete: {len(all_contracts)} contracts from "
        f"{sum(s.markets_found for s in statuses)} markets across "
        f"{len(statuses)} platforms."
    )

    hedge_pairs, spread_pairs = find_matching_pairs(all_contracts)
    logger.info(
        f"Matched {len(hedge_pairs)} hedge pairs and {len(spread_pairs)} spread pairs."
    )

    opportunities: list[ArbitrageOpportunity] = []

    for contract_a, contract_b, score, canonical in hedge_pairs:
        # Pinnacle is oracle-only — never a bettable leg.
        # After the namespace fix, Pinnacle h2h contracts share the same bucket as DK/Kalshi
        # and will form hedge pairs with them. Those pairs are used only for oracle construction
        # below; they must NOT be turned into visible arb opportunities.
        if (contract_a.platform == Platform.PINNACLE or
                contract_b.platform == Platform.PINNACLE):
            continue
        # Route by platform type:
        # • Both sportsbooks → cross-book "surebet" arb
        # • SB + PM or PM + PM → standard hedge arb
        if (contract_a.platform in SPORTSBOOK_PLATFORMS and
                contract_b.platform in SPORTSBOOK_PLATFORMS):
            opp = build_sportsbook_arb(contract_a, contract_b, score, canonical)
        else:
            opp = build_hedge_opportunity(contract_a, contract_b, score, canonical)
        if opp:
            opportunities.append(opp)

    # Build lookup: (parent_event_id, outcome_label) → opponent's outcome_label.
    # Collect team labels from ALL contracts (YES and NO sides) because Kalshi binary
    # games often have only one market per game — the second team's label only appears
    # as the outcome_label of the NO-side contract.  Including both sides ensures
    # every team label gets into the map regardless of which contract carries it.
    from collections import defaultdict
    _event_labels: dict[str, list[str]] = defaultdict(list)
    for c in all_contracts:
        if (c.outcome_label
                and c.outcome_label.lower() not in ("yes", "no", "")
                and c.parent_event_id):
            _event_labels[c.parent_event_id].append(c.outcome_label)

    opponent_label: dict[tuple[str, str], str] = {}
    for event_id, labels in _event_labels.items():
        unique = list(dict.fromkeys(labels))   # deduplicate, preserve order
        if len(unique) == 2:
            opponent_label[(event_id, unique[0])] = unique[1]
            opponent_label[(event_id, unique[1])] = unique[0]

    for contract_a, contract_b, score, canonical in spread_pairs:
        opp = build_spread_opportunity(contract_a, contract_b, score, canonical, opponent_label)
        if opp:
            opportunities.append(opp)

    # Keep only the best sportsbook per Kalshi contract for hedge arbs
    opportunities = _best_sb_per_kalshi_contract(opportunities)
    # Keep only the best sportsbook per Kalshi contract for spread arbs
    opportunities = _best_spread_per_pm_contract(opportunities)

    opportunities.sort(key=lambda o: (o.net_profit_pct, o.net_profit), reverse=True)

    seen: set[str] = set()
    deduped: list[ArbitrageOpportunity] = []

    for opp in opportunities:
        a = opp.leg_yes.contract
        b = opp.leg_no.contract
        event_slug = opp.event_title[:50].lower().strip()

        if opp.arb_type == "sportsbook":
            # Use sorted leg keys so (DK:BOS + FD:DEN) and (FD:DEN + DK:BOS)
            # produce the same dedup key and the duplicate is discarded.
            leg_keys = sorted([
                f"{a.platform.value}:{a.outcome_label}",
                f"{b.platform.value}:{b.outcome_label}",
            ])
            dedup_key = f"sportsbook|{event_slug}|{'|'.join(leg_keys)}"
        else:
            dedup_key = (
                f"{opp.arb_type}|{event_slug}|"
                f"{a.platform}:{a.outcome_label}:{opp.leg_yes.action}|"
                f"{b.platform}:{b.outcome_label}:{opp.leg_no.action}"
            )

        if dedup_key not in seen:
            seen.add(dedup_key)
            deduped.append(opp)

    logger.info(f"Found {len(deduped)} unique arbitrage opportunities.")

    # ── Shared oracle infrastructure (EV edge + value passes) ────────────────
    # Bettable US sportsbooks — Pinnacle is oracle-only, not a bettable leg.
    _BETTABLE_SB = {Platform.DRAFTKINGS, Platform.FANDUEL, Platform.CAESARS}

    # Pinnacle implied-probability lookup: (parent_event_id, is_yes_side) → prob (1/D)
    # Built once, shared between the EV edge weighting pass and the value pass.
    pinnacle_price_lookup: dict[tuple[str, bool], float] = {}
    for c in all_contracts:
        if (c.platform == Platform.PINNACLE
                and c.decimal_odds is not None
                and c.decimal_odds > 1.0
                and c.parent_event_id):
            pinnacle_price_lookup[(c.parent_event_id, c.is_yes_side)] = 1.0 / c.decimal_odds

    # SB oracle map: SB market_id → oracle implied prob for the SAME outcome.
    # Derived from SB+Pinnacle hedge pairs already found by the matcher.
    # Used to weight EV edge opportunities by the oracle's "true probability".
    sb_oracle_map: dict[str, float] = {}
    for contract_a, contract_b, _score, _ in hedge_pairs:
        is_pin_a = contract_a.platform == Platform.PINNACLE
        is_pin_b = contract_b.platform == Platform.PINNACLE
        if is_pin_a == is_pin_b:
            continue
        pin_c = contract_a if is_pin_a else contract_b
        sb_c  = contract_b if is_pin_a else contract_a
        if sb_c.platform not in _BETTABLE_SB:
            continue
        # Oracle key: oracle prob for the SAME direction as sb_c
        # (pin_c is the complement, so sb_c's direction = not pin_c.is_yes_side)
        oracle_key   = (pin_c.parent_event_id, not pin_c.is_yes_side)
        oracle_price = pinnacle_price_lookup.get(oracle_key)
        if oracle_price is not None and sb_c.market_id not in sb_oracle_map:
            sb_oracle_map[sb_c.market_id] = oracle_price

    logger.info(
        f"[oracle] pinnacle_price_lookup: {len(pinnacle_price_lookup)} entries | "
        f"sb_oracle_map: {len(sb_oracle_map)} SB contracts mapped to oracle prob."
    )

    # ── Soccer 3-way oracle pre-collection ───────────────────────────────────────
    # Soccer "not_X" ↔ "X" hedge pairs give us P(X) for each outcome individually.
    # We collect all 3 per game BEFORE building the oracle maps so that:
    #   oracle(not_X) = P(Y) + P(Z)   (sum of ALL OTHER outcomes)
    # instead of the incorrect 1 - P(X), which is only valid for binary markets.
    #
    # Why `1 - P(X)` is wrong for 3-way soccer:
    #   With vig, P(PSG) + P(NIC) + P(TIE) > 1 (e.g. 0.60 + 0.22 + 0.32 = 1.14).
    #   1 - P(PSG) = 0.40, but the actual P(not PSG) = P(NIC) + P(TIE) = 0.54.
    #   The 14pp error equals the full vig — large enough to produce significant
    #   false signals for "not_X" contracts (both false positives and negatives).
    #
    # Structure: parent_event_id → {pinnacle_outcome_label_lower → implied_prob}
    _soccer_pin_probs: dict[str, dict[str, float]] = {}
    for _ca, _cb, _s, _ in hedge_pairs:
        _is_pin_a = _ca.platform == Platform.PINNACLE
        _is_pin_b = _cb.platform == Platform.PINNACLE
        if _is_pin_a == _is_pin_b:
            continue
        _pin = _ca if _is_pin_a else _cb
        _pm  = _cb if _is_pin_a else _ca
        if _pm.platform not in (Platform.KALSHI, Platform.POLYMARKET):
            continue
        if _pin.decimal_odds is None or _pin.decimal_odds <= 1.0:
            continue
        _pm_lbl = (_pm.outcome_label or "").lower()
        if not _pm_lbl.startswith("not_"):
            continue   # only "not_X" ↔ "X" soccer pairs carry individual outcome probs
        _pin_lbl = (_pin.outcome_label or "").lower()
        _parent  = _pin.parent_event_id
        if _parent:
            if _parent not in _soccer_pin_probs:
                _soccer_pin_probs[_parent] = {}
            _soccer_pin_probs[_parent][_pin_lbl] = 1.0 / _pin.decimal_odds

    logger.info(
        f"[oracle] soccer pre-collection: {len(_soccer_pin_probs)} games, "
        f"{sum(len(v) for v in _soccer_pin_probs.values())} outcome probs."
    )

    # Kalshi oracle map: (market_id, is_yes_side) → Pinnacle implied prob for the SAME outcome.
    # Built from Kalshi+Pinnacle hedge pairs (complementary outcomes) already found
    # by the fuzzy matcher. Avoids parent_event_id namespace mismatch (Kalshi uses
    # abbreviations, Pinnacle uses full names) by keying on Kalshi market_id directly.
    #
    # IMPORTANT: Kalshi binary game YES and NO contracts share the same market_id
    # (both use the ticker of the first market, e.g. "KXNBAGAME-...-OKC" for both
    # the OKC YES and the DAL NO contracts of the same game). Keying by market_id
    # alone causes the oracle stored for OKC YES to overwrite (or block) the oracle
    # for DAL NO, giving DAL the wrong oracle price (OKC's 92% instead of DAL's 12%).
    # The compound key (market_id, is_yes_side) uniquely identifies each contract side.
    #
    # TWO-BRANCH ORACLE FORMULA:
    #
    # Branch A — Soccer "not_X" contracts (outcome_label starts with "not_"):
    #   oracle(not_X) = P(Y) + P(Z)  (sum of all other Pinnacle outcomes for this game)
    #   oracle(X YES) = P(X) directly from the same Pinnacle contract
    #   Both entries are written in the same iteration to avoid the complement pass
    #   for soccer (which would give wrong values due to vig: 1 - P(not_X) ≠ P(X)).
    #
    # Branch B — Binary contracts (e.g. outcome_label "OKC", "PHI", "YES", "NO"):
    #   oracle for outcome X = pinnacle_price_lookup[(parent, not pin_c.is_yes_side)].
    #   pin_c is the complement (covers outcome "not X"), so "not pin_c.is_yes_side"
    #   retrieves P(X wins) directly — the correct oracle with no vig deflation.
    kalshi_oracle_map: dict[tuple[str, bool], float] = {}
    for contract_a, contract_b, _score, _ in hedge_pairs:
        is_pin_a = contract_a.platform == Platform.PINNACLE
        is_pin_b = contract_b.platform == Platform.PINNACLE
        if is_pin_a == is_pin_b:
            continue
        pin_c = contract_a if is_pin_a else contract_b
        kal_c = contract_b if is_pin_a else contract_a
        if kal_c.platform != Platform.KALSHI:
            continue
        if pin_c.decimal_odds is None or pin_c.decimal_odds <= 1.0:
            continue
        kal_map_key = (kal_c.market_id, kal_c.is_yes_side)
        if kal_map_key not in kalshi_oracle_map:
            kal_lbl = (kal_c.outcome_label or "").lower()
            if kal_lbl.startswith("not_"):
                # Soccer "not_X": oracle = P(all other outcomes)
                base   = kal_lbl[4:]   # "not_psg" → "psg"
                parent = pin_c.parent_event_id
                probs  = _soccer_pin_probs.get(parent, {})
                if base in probs and len(probs) >= 2:
                    oracle_price = sum(p for lbl, p in probs.items() if lbl != base)
                    # Also directly set oracle for the X-YES contract = P(X)
                    yes_key = (kal_c.market_id, True)
                    if yes_key not in kalshi_oracle_map:
                        kalshi_oracle_map[yes_key] = probs[base]
                else:
                    # Fallback if pre-collection missed this game
                    oracle_price = 1.0 - (1.0 / pin_c.decimal_odds)
            else:
                # Binary game: look up P(kal_c outcome) from pinnacle_price_lookup;
                # pin_c is the complement so "not pin_c.is_yes_side" is kal_c's direction
                oracle_key   = (pin_c.parent_event_id, not pin_c.is_yes_side)
                oracle_price = pinnacle_price_lookup.get(oracle_key)
            if oracle_price is not None:
                kalshi_oracle_map[kal_map_key] = oracle_price

    logger.info(f"[oracle] kalshi_oracle_map: {len(kalshi_oracle_map)} Kalshi contracts mapped to oracle prob.")

    # Polymarket oracle map: (market_id, is_yes_side) → Pinnacle implied prob.
    # Same construction as kalshi_oracle_map but filters for Polymarket contracts.
    # Applies the same two-branch formula using the shared _soccer_pin_probs table.
    polymarket_oracle_map: dict[tuple[str, bool], float] = {}
    for contract_a, contract_b, _score, _ in hedge_pairs:
        is_pin_a = contract_a.platform == Platform.PINNACLE
        is_pin_b = contract_b.platform == Platform.PINNACLE
        if is_pin_a == is_pin_b:
            continue
        pin_c  = contract_a if is_pin_a else contract_b
        poly_c = contract_b if is_pin_a else contract_a
        if poly_c.platform != Platform.POLYMARKET:
            continue
        if pin_c.decimal_odds is None or pin_c.decimal_odds <= 1.0:
            continue
        poly_key = (poly_c.market_id, poly_c.is_yes_side)
        if poly_key not in polymarket_oracle_map:
            poly_lbl = (poly_c.outcome_label or "").lower()
            if poly_lbl.startswith("not_"):
                base   = poly_lbl[4:]
                parent = pin_c.parent_event_id
                probs  = _soccer_pin_probs.get(parent, {})
                if base in probs and len(probs) >= 2:
                    oracle_price = sum(p for lbl, p in probs.items() if lbl != base)
                    yes_key = (poly_c.market_id, True)
                    if yes_key not in polymarket_oracle_map:
                        polymarket_oracle_map[yes_key] = probs[base]
                else:
                    oracle_price = 1.0 - (1.0 / pin_c.decimal_odds)
            else:
                oracle_key   = (pin_c.parent_event_id, not pin_c.is_yes_side)
                oracle_price = pinnacle_price_lookup.get(oracle_key)
            if oracle_price is not None:
                polymarket_oracle_map[poly_key] = oracle_price

    logger.info(f"[oracle] polymarket_oracle_map: {len(polymarket_oracle_map)} Polymarket contracts mapped to oracle prob.")

    # ── Complement oracle pass ────────────────────────────────────────────────
    # For binary markets: Kalshi emits a YES (team A) and NO (team B) contract.
    # The hedge pair is (NO "team B", Pinnacle "team A") — so team A YES gets
    # no oracle entry from the main loop. Derive it as 1 − P(not A).
    #
    # Soccer YES contracts (PSG wins, NIC wins, TIE) are already set directly in
    # the oracle building loop above via the soccer_pin_probs pre-collection.
    # Those entries are already correct (P(X) from Pinnacle, not a vig-distorted
    # complement). The complement pass is a no-op for soccer games.
    #
    # Complement: for any (market_id, is_yes_side) that has an oracle but whose
    # mirror does not, derive mirror from 1 − oracle_p.
    # Only valid when 0 < oracle_p < 1 (degenerate prices carry no information).
    _kal_complements: dict[tuple[str, bool], float] = {}
    for (mkt_id, is_yes), oracle_p in kalshi_oracle_map.items():
        comp = (mkt_id, not is_yes)
        if comp not in kalshi_oracle_map and 0.0 < oracle_p < 1.0:
            _kal_complements[comp] = 1.0 - oracle_p
    kalshi_oracle_map.update(_kal_complements)
    if _kal_complements:
        logger.info(f"[oracle] complement pass: added {len(_kal_complements)} Kalshi complement entries (soccer YES side).")

    _poly_complements: dict[tuple[str, bool], float] = {}
    for (mkt_id, is_yes), oracle_p in polymarket_oracle_map.items():
        comp = (mkt_id, not is_yes)
        if comp not in polymarket_oracle_map and 0.0 < oracle_p < 1.0:
            _poly_complements[comp] = 1.0 - oracle_p
    polymarket_oracle_map.update(_poly_complements)
    if _poly_complements:
        logger.info(f"[oracle] complement pass: added {len(_poly_complements)} Polymarket complement entries.")

    # ── EV-edge pass: SB+PM and SB+SB pairs, weighted by Pinnacle oracle prob ─
    # Pinnacle contracts are excluded as bettable legs (oracle-only platform).
    # When Pinnacle oracle data is available, weighted_ev_pct uses Pinnacle's
    # implied probability as the "true probability" rather than the market's
    # own vig-inflated implied probability.
    ev_edges_raw: list[EvEdgeOpportunity] = []
    for contract_a, contract_b, score, canonical in hedge_pairs:
        # Pinnacle is oracle-only — never a bettable leg
        if (contract_a.platform == Platform.PINNACLE or
                contract_b.platform == Platform.PINNACLE):
            continue
        # Look up Pinnacle oracle prob for the SB leg (matches leg_a in build_ev_edge)
        is_sb_a = contract_a.platform in _BETTABLE_SB
        sb_market_id = contract_a.market_id if is_sb_a else contract_b.market_id
        oracle_prob  = sb_oracle_map.get(sb_market_id)
        ev = build_ev_edge(contract_a, contract_b, score, canonical, oracle_prob=oracle_prob)
        if ev:
            ev_edges_raw.append(ev)

    def _ev_dedup_key(ev: EvEdgeOpportunity) -> str:
        """Key by PM leg only — all SBs for the same Kalshi contract collapse to one entry.

        This mirrors _best_sb_per_kalshi_contract: DraftKings+Kalshi and
        FanDuel+Kalshi for the same event share a key, so only the best
        weighted_ev_pct is kept.
        """
        pm = ev.pm_leg.contract
        return f"{pm.platform.value}:{pm.market_id}:{pm.side}"

    # Deduplicate: keep the best weighted_ev_pct per unique pair
    best_ev: dict[str, EvEdgeOpportunity] = {}
    for ev in ev_edges_raw:
        key = _ev_dedup_key(ev)
        if key not in best_ev or ev.weighted_ev_pct > best_ev[key].weighted_ev_pct:
            best_ev[key] = ev

    ev_edges = sorted(best_ev.values(), key=lambda e: e.weighted_ev_pct, reverse=True)
    logger.info(f"Found {len(ev_edges)} unique EV-edge opportunities.")

    # ── Value pass: Kalshi bets mispriced vs Pinnacle oracle ──────────────────
    # Only Kalshi vs oracle comparisons are shown in the Value tab.
    # SB vs oracle comparisons were removed: the Opening Line oracle can be stale
    # for retail-book comparisons, and the SB value signal is lower-quality.
    #
    # For each Kalshi+oracle hedge pair, look up the oracle probability for the
    # SAME outcome as the Kalshi contract. If Kalshi's price (implied prob) is
    # below the oracle true probability, it's a positive-EV Kalshi buy.
    value_ops_raw: list[ValueOpportunity] = []
    _val_kal_total    = 0
    _val_kal_found    = 0
    _val_kal_missing  = 0
    _val_kal_passed   = 0
    _val_kal_filtered = 0

    _PM_ORACLE_MAPS = {
        Platform.KALSHI:     kalshi_oracle_map,
        Platform.POLYMARKET: polymarket_oracle_map,
    }

    for contract_a, contract_b, score, canonical in hedge_pairs:
        is_pin_a = contract_a.platform == Platform.PINNACLE
        is_pin_b = contract_b.platform == Platform.PINNACLE

        if is_pin_a == is_pin_b:
            continue

        pin_c = contract_a if is_pin_a else contract_b
        pm_c  = contract_b if is_pin_a else contract_a

        pm_oracle_map = _PM_ORACLE_MAPS.get(pm_c.platform)
        if pm_oracle_map is None:
            continue  # Not a supported PM platform for value

        _val_kal_total += 1

        oracle_price = pm_oracle_map.get((pm_c.market_id, pm_c.is_yes_side))
        if oracle_price is None:
            _val_kal_missing += 1
            logger.debug(
                f"[value/pm] oracle missing — {pm_c.platform} market_id={pm_c.market_id!r} is_yes={pm_c.is_yes_side} event={canonical!r}"
            )
            continue

        _val_kal_found += 1
        val = build_value_opportunity(
            pm_c, oracle_price, score, canonical,
            oracle_platform=Platform.PINNACLE,
        )
        if val:
            _val_kal_passed += 1
            value_ops_raw.append(val)
        else:
            _val_kal_filtered += 1

    logger.info(
        f"[value/pm] pipeline: {_val_kal_total} PM+oracle pairs → "
        f"{_val_kal_found} oracle found ({_val_kal_missing} missing) → "
        f"{_val_kal_passed} passed filters ({_val_kal_filtered} EV/edge filtered)"
    )

    # ── Complement value pass ─────────────────────────────────────────────────
    # Soccer YES contracts (e.g. "SJ to win") have oracle entries derived above
    # via the complement pass, but they never appear in hedge_pairs so the main
    # loop above never sees them.  Walk all_contracts directly to find them.
    # Use the sister contract's hedge-pair score/canonical for display context.
    if _kal_complements or _poly_complements:
        _pm_by_key: dict[tuple[str, bool], "MarketContract"] = {
            (c.market_id, c.is_yes_side): c
            for c in all_contracts
            if c.platform in (Platform.KALSHI, Platform.POLYMARKET)
        }
        # Build score/canonical lookup from hedge pairs (sister = not is_yes_side)
        _pm_pair_info: dict[tuple[str, bool], tuple[float, str]] = {}
        for ca, cb, score, canonical in hedge_pairs:
            is_pin_a = ca.platform == Platform.PINNACLE
            is_pin_b = cb.platform == Platform.PINNACLE
            if is_pin_a == is_pin_b:
                continue
            pm_c = cb if is_pin_a else ca
            if pm_c.platform in (Platform.KALSHI, Platform.POLYMARKET):
                _pm_pair_info[(pm_c.market_id, pm_c.is_yes_side)] = (score, canonical)

        _comp_passed = 0
        _comp_map = {**_kal_complements, **_poly_complements}
        for (mkt_id, is_yes) in _comp_map:
            contract = _pm_by_key.get((mkt_id, is_yes))
            if contract is None:
                continue
            oracle_price = _PM_ORACLE_MAPS[contract.platform].get((mkt_id, is_yes))
            if oracle_price is None:
                continue
            # Inherit score/canonical from the sister contract's hedge pair
            sister_key = (mkt_id, not is_yes)
            score, canonical = _pm_pair_info.get(sister_key, (0.90, contract.event_title or ""))
            val = build_value_opportunity(
                contract, oracle_price, score, canonical,
                oracle_platform=Platform.PINNACLE,
            )
            if val:
                _comp_passed += 1
                value_ops_raw.append(val)
        if _comp_passed:
            logger.info(f"[value/pm] complement pass: {_comp_passed} additional value ops from soccer/complement YES contracts.")

    # Deduplicate: best edge_ppts per unique bet contract (SB or Kalshi)
    # Sort by edge_ppts ("variance to true probability") so the widest gap
    # between oracle true prob and bet implied prob surfaces first.
    best_val: dict[str, ValueOpportunity] = {}
    for val in value_ops_raw:
        c = val.sb_leg.contract
        # Include is_yes_side so OKC YES and DAL NO (same market_id on Kalshi) are
        # treated as separate opportunities and not collapsed into one entry where the
        # false-positive high-edge one would displace the genuine lower-edge one.
        key = f"{c.platform.value}:{c.market_id}:{c.is_yes_side}"
        if key not in best_val or val.edge_ppts > best_val[key].edge_ppts:
            best_val[key] = val

    value_ops = sorted(best_val.values(), key=lambda v: v.edge_ppts, reverse=True)
    logger.info(f"Found {len(value_ops)} unique cross-market value opportunities.")

    # ── Kalshi sport counts (for the Value Test tab sport-selector UI) ────────
    # Count unique games per sport slug from Kalshi YES-side contracts only
    # (avoids double-counting: binary games have 2 contracts, soccer games have 6).
    # parent_event_id = "nba okc tor" → first word = sport slug.
    from collections import defaultdict
    kalshi_events: dict[str, set] = defaultdict(set)
    for c in all_contracts:
        if (c.platform == Platform.KALSHI
                and c.is_yes_side
                and c.parent_event_id):
            parts = c.parent_event_id.split()
            if parts:
                kalshi_events[parts[0]].add(c.parent_event_id)
    kalshi_sport_counts: dict[str, int] = {
        slug: len(event_ids) for slug, event_ids in kalshi_events.items()
    }
    logger.info(
        f"[kalshi_counts] {sum(kalshi_sport_counts.values())} unique games across "
        f"{len(kalshi_sport_counts)} sports: "
        + ", ".join(f"{k}:{v}" for k, v in sorted(kalshi_sport_counts.items()))
    )

    return deduped, ev_edges, value_ops, statuses, kalshi_sport_counts


async def scan_sharp_value(
    api_key:    str,
    sport_keys: list[str] | None = None,
) -> tuple[list[ValueOpportunity], list[ScannerStatus], int]:
    """
    Force-scan-only pipeline: Kalshi vs real Pinnacle oracle (The Odds API).

    Steps:
      1. Run KalshiScanner and OddsApiPinnacleOracle concurrently.
      2. Combine contracts and find hedge pairs.
      3. Build pinnacle_price_lookup from Pinnacle contracts.
      4. Build kalshi_oracle_map from Kalshi↔Pinnacle hedge pairs (same logic
         as run_full_scan, reused verbatim).
      5. Value pass: emit ValueOpportunity for each Kalshi contract whose
         Odds API Pinnacle oracle probability exceeds its own implied price.
      6. Return (value_ops, statuses, requests_remaining).

    Never called by the 90-s scheduler — exclusively triggered by
    POST /api/sharp-value/scan.
    """
    import httpx
    from scanners.odds_api_pinnacle import OddsApiPinnacleOracle, PINNACLE_SPORT_KEYS

    all_contracts: list = []
    statuses: list[ScannerStatus] = []
    requests_remaining = 500

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        # Run Kalshi scanner and Odds API Pinnacle oracle concurrently
        kalshi_scanner = KalshiScanner(client)
        oracle         = OddsApiPinnacleOracle(client)

        kalshi_task = kalshi_scanner.scan()
        oracle_task = oracle.fetch_markets(api_key, sport_keys or PINNACLE_SPORT_KEYS)

        kalshi_result, oracle_result = await asyncio.gather(
            kalshi_task, oracle_task, return_exceptions=True
        )

    # Unpack Kalshi results
    if isinstance(kalshi_result, Exception):
        logger.error(f"[sharp_value] KalshiScanner failed: {kalshi_result}")
        kalshi_status = ScannerStatus(
            platform=Platform.KALSHI,
            error=str(kalshi_result),
            is_healthy=False,
        )
        statuses.append(kalshi_status)
    else:
        kal_contracts, kal_status = kalshi_result
        all_contracts.extend(kal_contracts)
        statuses.append(kal_status)

    # Unpack Pinnacle oracle results
    if isinstance(oracle_result, Exception):
        logger.error(f"[sharp_value] OddsApiPinnacleOracle failed: {oracle_result}")
        oracle_status = ScannerStatus(
            platform=Platform.PINNACLE,
            error=str(oracle_result),
            is_healthy=False,
        )
        statuses.append(oracle_status)
    else:
        pin_contracts, requests_remaining = oracle_result
        all_contracts.extend(pin_contracts)
        statuses.append(ScannerStatus(
            platform=Platform.PINNACLE,
            markets_found=len(pin_contracts),
            is_healthy=True,
        ))

    logger.info(
        f"[sharp_value] {len(all_contracts)} total contracts "
        f"({sum(s.markets_found for s in statuses)} markets)."
    )

    hedge_pairs, _ = find_matching_pairs(all_contracts)
    logger.info(f"[sharp_value] {len(hedge_pairs)} hedge pairs matched.")

    # Build pinnacle_price_lookup — identical logic to run_full_scan
    pinnacle_price_lookup: dict[tuple[str, bool], float] = {}
    for c in all_contracts:
        if (c.platform == Platform.PINNACLE
                and c.decimal_odds is not None
                and c.decimal_odds > 1.0
                and c.parent_event_id):
            pinnacle_price_lookup[(c.parent_event_id, c.is_yes_side)] = 1.0 / c.decimal_odds

    # Soccer pre-collection — same logic as run_full_scan.
    # Collects all 3 Pinnacle implied probs per soccer game so that
    # oracle(not_X) = P(Y) + P(Z) instead of the vig-wrong 1 - P(X).
    _soccer_pin_probs: dict[str, dict[str, float]] = {}
    for _ca, _cb, _s, _ in hedge_pairs:
        _is_pin_a = _ca.platform == Platform.PINNACLE
        _is_pin_b = _cb.platform == Platform.PINNACLE
        if _is_pin_a == _is_pin_b:
            continue
        _pin = _ca if _is_pin_a else _cb
        _pm  = _cb if _is_pin_a else _ca
        if _pm.platform != Platform.KALSHI:
            continue
        if _pin.decimal_odds is None or _pin.decimal_odds <= 1.0:
            continue
        _pm_lbl = (_pm.outcome_label or "").lower()
        if not _pm_lbl.startswith("not_"):
            continue
        _pin_lbl = (_pin.outcome_label or "").lower()
        _parent  = _pin.parent_event_id
        if _parent:
            if _parent not in _soccer_pin_probs:
                _soccer_pin_probs[_parent] = {}
            _soccer_pin_probs[_parent][_pin_lbl] = 1.0 / _pin.decimal_odds

    # Build kalshi_oracle_map — same two-branch formula as run_full_scan.
    kalshi_oracle_map: dict[tuple[str, bool], float] = {}
    for contract_a, contract_b, _score, _ in hedge_pairs:
        is_pin_a = contract_a.platform == Platform.PINNACLE
        is_pin_b = contract_b.platform == Platform.PINNACLE
        if is_pin_a == is_pin_b:
            continue
        pin_c = contract_a if is_pin_a else contract_b
        kal_c = contract_b if is_pin_a else contract_a
        if kal_c.platform != Platform.KALSHI:
            continue
        if pin_c.decimal_odds is None or pin_c.decimal_odds <= 1.0:
            continue
        kal_map_key = (kal_c.market_id, kal_c.is_yes_side)
        if kal_map_key not in kalshi_oracle_map:
            kal_lbl = (kal_c.outcome_label or "").lower()
            if kal_lbl.startswith("not_"):
                base   = kal_lbl[4:]
                parent = pin_c.parent_event_id
                probs  = _soccer_pin_probs.get(parent, {})
                if base in probs and len(probs) >= 2:
                    oracle_price = sum(p for lbl, p in probs.items() if lbl != base)
                    yes_key = (kal_c.market_id, True)
                    if yes_key not in kalshi_oracle_map:
                        kalshi_oracle_map[yes_key] = probs[base]
                else:
                    oracle_price = 1.0 - (1.0 / pin_c.decimal_odds)
            else:
                oracle_key   = (pin_c.parent_event_id, not pin_c.is_yes_side)
                oracle_price = pinnacle_price_lookup.get(oracle_key)
            if oracle_price is not None:
                kalshi_oracle_map[kal_map_key] = oracle_price

    logger.info(
        f"[sharp_value] kalshi_oracle_map: {len(kalshi_oracle_map)} entries | "
        f"pinnacle_price_lookup: {len(pinnacle_price_lookup)} entries"
    )

    # Value pass — identical logic to run_full_scan
    value_ops_raw: list[ValueOpportunity] = []
    for contract_a, contract_b, score, canonical in hedge_pairs:
        is_pin_a = contract_a.platform == Platform.PINNACLE
        is_pin_b = contract_b.platform == Platform.PINNACLE
        if is_pin_a == is_pin_b:
            continue
        pin_c = contract_a if is_pin_a else contract_b
        kal_c = contract_b if is_pin_a else contract_a
        if kal_c.platform != Platform.KALSHI:
            continue
        oracle_price = kalshi_oracle_map.get((kal_c.market_id, kal_c.is_yes_side))
        if oracle_price is None:
            continue
        val = build_value_opportunity(
            kal_c, oracle_price, score, canonical,
            oracle_platform=Platform.PINNACLE,
        )
        if val:
            value_ops_raw.append(val)

    # Deduplicate: best edge_ppts per Kalshi contract side
    best_val: dict[str, ValueOpportunity] = {}
    for val in value_ops_raw:
        c   = val.sb_leg.contract
        key = f"{c.platform.value}:{c.market_id}:{c.is_yes_side}"
        if key not in best_val or val.edge_ppts > best_val[key].edge_ppts:
            best_val[key] = val

    value_ops = sorted(best_val.values(), key=lambda v: v.edge_ppts, reverse=True)
    logger.info(
        f"[sharp_value] {len(value_ops)} unique value opportunities found. "
        f"{requests_remaining} Odds API requests remaining."
    )
    return value_ops, statuses, requests_remaining
