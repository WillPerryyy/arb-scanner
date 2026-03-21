"""
Cross-platform event matching — Kalshi + DraftKings + FanDuel + Caesars.

Valid arbitrage structures:

  1. Cross-platform hedge arb (SB + Kalshi, DIFFERENT outcomes):
       Buy Team A on a sportsbook (DK / FD / Caesars) + Buy Team B on Kalshi.
       Exactly one leg pays out. Arb if total_cost < guaranteed_payout.

  2. Intra-Kalshi hedge arb (same binary event, YES + NO both on Kalshi):
       Arb when the two sides are mispriced relative to the $1 payout.

  3. Cross-sportsbook arb / "surebet" (SB+SB, DIFFERENT outcomes):
       Buy Team A on DraftKings + Buy Team B on FanDuel (or any SB pair).
       Both legs are BUY orders at decimal odds D_A and D_B.
       Arb iff 1/D_A + 1/D_B < 1  (implied probability sum < 100%).
       Payout convention: ALL supported sportsbooks use European decimal odds
       where the stake is included in the return (total = stake × decimal_odds).

  4. Same-side spread arb (intra-Kalshi only):
       Buy YES on one Kalshi market, sell YES on another (same underlying).
       SB+SB spreads are excluded (buying same outcome twice doubles risk).

Matching strategy:
  Phase 1 — exact parent_event_id bucket:
    normalize_event_key("{sport} {home_abbr} {away_abbr}") is used by all
    scanners, producing identical keys for the same game across platforms.
    e.g. DraftKings "nba OKC TOR" → "nba okc tor" = Kalshi "nba OKC TOR".

  Phase 2 — fuzzy cross-bucket matching:
    token_set_ratio handles subset matches for events with different title
    verbosity (e.g. "raptors thunder" vs "city oklahoma raptors thunder toronto").

Outcome resolution:
  All sportsbooks emit team abbreviations as outcome_label ("OKC", "TOR").
  Kalshi binary game events use the same abbreviations from ticker suffixes.
  Kalshi soccer 3-way events use:
    YES contracts: outcome_label = team abbr or "TIE"
    NO  contracts: outcome_label = "not_" + abbr  ("not_SD", "not_TIE", "not_STL")
  The "not_X" label convention enables the matcher to distinguish 3-way
  complementarity from binary complementarity:
    • Binary: "OKC" vs "TOR" are DIFFERENT → implicitly complementary (one must win)
    • 3-way:  "SD"  vs "TIE" are DIFFERENT → NOT complementary (STL could win)
              "SD"  vs "not_SD" are EXPLICIT complements (only valid soccer hedge)
  MarketContract.num_outcomes > 2 signals a 3-way market; the hedge-pair
  validator requires an explicit "not_X" ↔ "X" match in that case.
"""
from __future__ import annotations
import re
import logging
from collections import defaultdict

from rapidfuzz import fuzz

from models import MarketContract, Platform
from config import settings

logger = logging.getLogger(__name__)

STOPWORDS = frozenset({
    "will", "the", "a", "an", "in", "on", "at", "to", "of", "be",
    "is", "does", "by", "for", "vs", "versus", "or", "and", "who",
    "which", "what", "when", "if", "that", "their", "win", "who",
    "get", "have", "with",
})

# Active platforms — extend when adding new sportsbooks or prediction markets
# Pinnacle is included so the matcher forms pairs with DK/FD/Caesars for the
# value-bet oracle pass (Pinnacle's lines are used as the sharp-line reference).
SPORTSBOOK_PLATFORMS = {Platform.DRAFTKINGS, Platform.FANDUEL, Platform.CAESARS, Platform.PINNACLE}
PREDICTION_MARKET_PLATFORMS = {Platform.KALSHI, Platform.POLYMARKET}


def normalize_event_key(title: str) -> str:
    """Produce a bag-of-words canonical key from an event title."""
    title = title.lower()
    title = re.sub(r"\[.*?\]", "", title)
    title = re.sub(r"[^a-z0-9\s]", " ", title)
    words = [w for w in title.split() if w and w not in STOPWORDS]
    return " ".join(sorted(words))


def _base_market_id(market_id: str) -> str:
    return market_id[:-3] if market_id.endswith("_no") else market_id


def _resolve_generic_is_yes(specific: MarketContract, generic: MarketContract) -> bool | None:
    """
    Given a specific-label contract (e.g. DK "Hawks") and a generic Yes/No contract
    (e.g. Kalshi "Yes"/"No"), determine whether the specific label maps to the YES side
    of the generic market by inspecting the generic market's event title.

    Splits on 'vs', '@', 'at' — the first token is team_yes, second is team_no.
    Checks if spec_label is a substring of team_yes (handles "Thunder" in
    "Oklahoma City Thunder") or vice versa.

    Returns True  → specific outcome == YES side of generic market
            False → specific outcome == NO side
            None  → cannot determine (no vs/@/at in title)
    """
    spec_lbl = (specific.outcome_label or "").lower().strip()
    generic_title = (generic.parent_event_title or generic.event_title).lower()
    teams_in_title = re.split(r"\s+(?:vs\.?|@|at)\s+", generic_title)
    if len(teams_in_title) < 2:
        return None
    team_yes = teams_in_title[0].strip()
    # Use substring match to handle "Thunder" ↔ "Oklahoma City Thunder"
    return (spec_lbl in team_yes or team_yes in spec_lbl) if spec_lbl and team_yes else None


def _contracts_cover_same_outcome(a: MarketContract, b: MarketContract) -> bool | None:
    """
    Returns True  if a and b represent the same outcome (same team wins),
            False if they represent different (complementary) outcomes,
            None  if undetermined.

    Handles four label configurations:
      1. Both generic (Yes/No/""): compare is_yes_side flags.
      2. "not_X" prefix (soccer 3-way): one or both labels start with "not_".
           "not_SD" vs "SD"     → False (complementary — strictly "not X" vs "X")
           "not_SD" vs "not_SD" → True  (both cover "SD doesn't win")
           "not_SD" vs "TIE"    → None  (undetermined — TIE ≠ complement of SD)
      3. Both specific team names: compare labels with substring support to
         handle full vs short name format differences across platforms.
         e.g. "Thunder" == "Oklahoma City Thunder" via substring check.
      4. Mixed (one generic, one specific): use _resolve_generic_is_yes.

    NOTE: The "not_X" block must be checked BEFORE the general specific-label
    substring test, which would otherwise produce "sd" in "not_sd" = True (false
    positive treating a complement as a same-outcome match).
    """
    a_lbl = (a.outcome_label or "").lower().strip()
    b_lbl = (b.outcome_label or "").lower().strip()
    generic_lbls = {"yes", "no", ""}

    a_generic = a_lbl in generic_lbls
    b_generic = b_lbl in generic_lbls

    # ── Both generic: use is_yes_side ────────────────────────────────────────
    if a_generic and b_generic:
        return a.is_yes_side == b.is_yes_side

    # ── "not_X" prefix: soccer 3-way complement labels ───────────────────────
    # Must come BEFORE the general specific-label block to avoid the false
    # positive: "sd" in "not_sd" = True (Python substring, not semantic match).
    a_not = a_lbl.startswith("not_")
    b_not = b_lbl.startswith("not_")
    if a_not or b_not:
        if a_not and b_not:
            # Both "not_X": same iff their base labels match
            # e.g. "not_sd" vs "not_sd" → True; "not_sd" vs "not_stl" → False
            return a_lbl[4:] == b_lbl[4:]
        # One is "not_X", the other is a specific label Y
        not_lbl = a_lbl if a_not else b_lbl
        pos_lbl = b_lbl if a_not else a_lbl
        base = not_lbl[4:]   # "not_sd" → "sd"
        if base == pos_lbl:
            return False   # "not_X" vs "X": strictly complementary
        # "not_X" vs "Y" where Y ≠ X: relationship is undetermined.
        # They are neither the same outcome, nor a clean binary complement.
        return None

    # ── Both specific team names ──────────────────────────────────────────────
    if not a_generic and not b_generic:
        a_base = a_lbl.removeprefix("no ").strip()
        b_base = b_lbl.removeprefix("no ").strip()
        if a_base == b_base:
            return True
        # Substring match handles full-name vs short-name mismatches:
        #   "thunder" in "oklahoma city thunder" → True (same team)
        #   "raptors" in "oklahoma city thunder" → False (different teams)
        # Guard: require min 3 chars to avoid false matches on short tokens.
        if len(a_base) >= 3 and len(b_base) >= 3:
            if a_base in b_base or b_base in a_base:
                return True
        return False

    # ── Mixed: one specific, one generic Yes/No ───────────────────────────────
    specific = a if not a_generic else b
    generic_c = b if not a_generic else a
    spec_is_yes = _resolve_generic_is_yes(specific, generic_c)
    if spec_is_yes is None:
        return None
    # If specific outcome IS the YES side, both contracts cover same outcome
    # iff the generic contract is also the YES side.
    if spec_is_yes:
        return specific.is_yes_side == generic_c.is_yes_side
    else:
        return specific.is_yes_side != generic_c.is_yes_side


def _market_types_compatible(a: MarketContract, b: MarketContract) -> bool:
    """
    Spread and total contracts may only pair with the same market type.
    Moneyline and prediction markets (Kalshi/Polymarket) may cross-pair freely.

    This prevents fuzzy-matched false arbs where an OddsAPI FanDuel spread
    bucket fuzzy-matches with a BetMGM h2h bucket (same game tokens ⊂ spread tokens
    → token_set_ratio = 100) and generates a fake sportsbook spread-vs-moneyline arb.
    """
    ta, tb = a.market_type, b.market_type
    if ta in ("spread", "total") or tb in ("spread", "total"):
        return ta == tb
    return True


def _is_valid_hedge_pair(a: MarketContract, b: MarketContract) -> bool:
    """
    Different platforms, complementary outcomes (exactly one must pay out).

    Accepts three structures:
      • SB + PM  (e.g. DraftKings + Kalshi) — classic cross-platform hedge
      • PM + PM  (e.g. Kalshi YES + Kalshi NO) — intra-Kalshi binary
      • SB + SB  (e.g. DraftKings + FanDuel)  — cross-sportsbook "surebet"

    3-way soccer markets require explicit "not_X" ↔ "X" complementarity:
      In a binary market, any two different outcomes are complementary (one
      must win). In a 3-way market (Home/Draw/Away), two different positives
      like "SD" vs "TIE" are NOT complementary — both can lose if STL wins.
      Only "SD" ↔ "not_SD" is a valid hedge for 3-way markets.
      This is signalled by MarketContract.num_outcomes > 2.

    Arb condition for each structure is evaluated in calculator.py;
    this function only checks structural validity.
    """
    if a.platform == b.platform:
        return False

    if not _market_types_compatible(a, b):
        return False

    # 3-way soccer: enforce explicit "not_X" ↔ "X" complementarity.
    # Bypass the general _contracts_cover_same_outcome logic, which treats
    # any two different specific labels as complementary (valid for binary
    # sports but not for 3-outcome markets).
    if a.num_outcomes > 2 or b.num_outcomes > 2:
        a_lbl = (a.outcome_label or "").lower().strip()
        b_lbl = (b.outcome_label or "").lower().strip()
        # Exactly one of the pair must be "not_X" and the other must be "X"
        if a_lbl.startswith("not_") and a_lbl[4:] == b_lbl:
            return True
        if b_lbl.startswith("not_") and b_lbl[4:] == a_lbl:
            return True
        return False

    same = _contracts_cover_same_outcome(a, b)
    if same is None:
        # Fall back to is_yes_side: hedge if one is YES and the other is NO
        return a.is_yes_side != b.is_yes_side
    return not same


def _is_valid_spread_pair(a: MarketContract, b: MarketContract) -> bool:
    """
    Different platforms, SAME outcome, at least one prediction-market platform.
    Sportsbooks are always the buy leg; only prediction markets can be sold.
    """
    if a.platform == b.platform:
        return False
    # Need at least one PM to enable selling
    if a.platform in SPORTSBOOK_PLATFORMS and b.platform in SPORTSBOOK_PLATFORMS:
        return False
    if not _market_types_compatible(a, b):
        return False
    same = _contracts_cover_same_outcome(a, b)
    if same is None:
        return False
    return same


def find_matching_pairs(
    all_contracts: list[MarketContract],
) -> tuple[
    list[tuple[MarketContract, MarketContract, float, str]],
    list[tuple[MarketContract, MarketContract, float, str]],
]:
    """
    Returns:
      hedge_pairs:  (a, b, score, title) where a and b are complementary outcomes
      spread_pairs: (a, b, score, title) where a and b are the same outcome
    """
    for c in all_contracts:
        if not c.parent_event_id:
            c.parent_event_id = normalize_event_key(c.parent_event_title or c.event_title)

    groups: dict[str, list[MarketContract]] = defaultdict(list)
    for c in all_contracts:
        groups[c.parent_event_id].append(c)

    hedge_results:  list[tuple[MarketContract, MarketContract, float, str]] = []
    spread_results: list[tuple[MarketContract, MarketContract, float, str]] = []
    seen_hedge:  set[frozenset[str]] = set()
    seen_spread: set[frozenset[str]] = set()

    def _key(c: MarketContract) -> str:
        return f"{c.platform}:{c.market_id}:{c.side}"

    def _title(a: MarketContract, b: MarketContract) -> str:
        ta = a.parent_event_title or a.event_title
        tb = b.parent_event_title or b.event_title
        return ta if len(ta) >= len(tb) else tb

    def _add_hedge(a: MarketContract, b: MarketContract, score: float) -> None:
        k = frozenset([_key(a), _key(b)])
        if k in seen_hedge or not _is_valid_hedge_pair(a, b):
            return
        seen_hedge.add(k)
        hedge_results.append((a, b, score, _title(a, b)))

    def _add_spread(a: MarketContract, b: MarketContract, score: float) -> None:
        k = frozenset([_key(a), _key(b)])
        if k in seen_spread or not _is_valid_spread_pair(a, b):
            return
        seen_spread.add(k)
        spread_results.append((a, b, score, _title(a, b)))

    # ── Phase 1: exact bucket ─────────────────────────────────────────────────
    for key, group in groups.items():
        if len(set(c.platform for c in group)) < 2:
            continue
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                _add_hedge(a, b, 1.0)
                _add_spread(a, b, 1.0)

    # ── Phase 2: fuzzy cross-bucket ───────────────────────────────────────────
    threshold = settings.FUZZY_MATCH_THRESHOLD
    group_platforms = {k: set(c.platform for c in v) for k, v in groups.items()}
    MAX_GROUP_SIZE_FOR_FUZZY = 50
    MIN_KEY_WORDS = 2
    eligible = [
        k for k in groups
        if len(k.split()) >= MIN_KEY_WORDS and len(groups[k]) <= MAX_GROUP_SIZE_FOR_FUZZY
    ]

    for i in range(len(eligible)):
        for j in range(i + 1, len(eligible)):
            ka, kb = eligible[i], eligible[j]
            pa, pb = group_platforms[ka], group_platforms[kb]
            # Skip if both groups have only one platform and it's the same
            if pa == pb and len(pa) == 1:
                continue
            # Skip if word-count ratio is too extreme (unlikely same event)
            wa, wb = len(ka.split()), len(kb.split())
            if max(wa, wb) > 2 * min(wa, wb) + 2:
                continue
            score = fuzz.token_set_ratio(ka, kb) / 100.0
            if score < threshold:
                continue
            for a in groups[ka]:
                for b in groups[kb]:
                    _add_hedge(a, b, score)
                    _add_spread(a, b, score)

    logger.info(
        f"Matcher: {len(all_contracts)} contracts → {len(groups)} groups → "
        f"{len(hedge_results)} hedge pairs, {len(spread_results)} spread pairs"
    )
    return hedge_results, spread_results
