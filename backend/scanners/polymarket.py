"""
Polymarket Gamma API — public, no key required.
https://gamma-api.polymarket.com/markets
Binary markets pay $1.00 per winning share.

Namespace alignment:
  Kalshi / DraftKings / FanDuel / Caesars all use Action Network abbreviations
  as their parent_event_id: normalize_event_key("nba OKC TOR") → "nba okc tor".
  Polymarket questions use full team names ("Thunder", "Oklahoma City Thunder") or
  generic Yes/No labels, which after normalize_event_key produce totally different
  strings that never fuzzy-match Kalshi/DK events.

  Fix: when both outcome labels can be mapped to AN abbreviations AND a sport can
  be identified, build parent_event_id = normalize_event_key("{sport} {a} {b}")
  and use the abbreviations as outcome_label values — making them land in the same
  exact bucket as the corresponding Kalshi/DK contracts.

  For non-sports markets (politics, crypto, macroeconomic) the question-based
  parent_event_id is kept; these may still match Kalshi markets via fuzzy matching
  if both platforms ask the same question with overlapping vocabulary.
"""
from __future__ import annotations
import re
import json

from scanners.base import BaseScanner
from arbitrage.matcher import normalize_event_key
from models import MarketContract, Platform, ContractSide

GAMMA_BASE = "https://gamma-api.polymarket.com"
PAYOUT = 1.0
MAX_PAGES = 2  # Fetch top 200 markets by volume — limits scan time within the 30s client timeout

# ── Sport detection ───────────────────────────────────────────────────────────
_SPORT_RE = re.compile(r'\b(nba|nfl|nhl|mlb|mls|wnba)\b', re.IGNORECASE)

# ── Spread / total market detector ────────────────────────────────────────────
# Questions containing these patterns are NOT simple moneylines (who wins).
# We must NOT put them in the abbreviation-based namespace ("nba okc tor")
# because the DK/Kalshi/Pinnacle h2h contracts in that bucket are moneylines.
# Pairing a Polymarket spread with a sportsbook moneyline is NOT a true hedge
# (if OKC wins by 3 against a -7.5 spread, both legs lose).
_SPREAD_RE = re.compile(
    r"""
    # Explicit spread/handicap language
    \bcover[s]?\b | \bspread\b | \bhandicap\b |
    # Total / O/U language
    \btotal\b | \bover\b | \bunder\b | \bo/u\b |
    # Point margin language ("by X", "X+ points", "+N", "-N pts")
    \bby\s+\d | \bby\s+more | \bby\s+at\s+least |
    \d+\s*\+\s*points? | \bpoints?\s+\d |
    # Numeric handicap with +/- (e.g. "+7.5", "-3.5", "7.5+")
    [+\-]\d+\.?\d*\b | \b\d+\.5\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

# ── Per-sport nickname → Action Network abbreviation ─────────────────────────
# Each entry maps the short team nickname (or common alias) as it appears in
# Polymarket outcome labels to the 2–4-letter AN abbreviation used by DK/Kalshi.
_SPORT_NICKS: dict[str, dict[str, str]] = {
    "nba": {
        "hawks": "atl",    "celtics": "bos",       "nets": "bkn",
        "hornets": "cha",  "bulls": "chi",          "cavaliers": "cle",
        "cavs": "cle",     "mavericks": "dal",      "mavs": "dal",
        "nuggets": "den",  "pistons": "det",        "warriors": "gsw",
        "rockets": "hou",  "pacers": "ind",         "clippers": "lac",
        "lakers": "lal",   "grizzlies": "mem",      "heat": "mia",
        "bucks": "mil",    "timberwolves": "min",   "wolves": "min",
        "pelicans": "nop", "knicks": "nyk",         "thunder": "okc",
        "magic": "orl",    "76ers": "phi",          "sixers": "phi",
        "suns": "phx",     "blazers": "por",        "kings": "sac",
        "spurs": "sas",    "raptors": "tor",        "jazz": "uta",
        "wizards": "was",
    },
    "nhl": {
        "ducks": "ana",      "coyotes": "ari",      "bruins": "bos",
        "sabres": "buf",     "flames": "cgy",       "hurricanes": "car",
        "blackhawks": "chi", "avalanche": "col",    "jackets": "cbj",
        "stars": "dal",      "wings": "det",        "oilers": "edm",
        "panthers": "fla",   "kings": "lak",        "wild": "min",
        "canadiens": "mtl",  "habs": "mtl",         "predators": "nsh",
        "preds": "nsh",      "devils": "njd",       "islanders": "nyi",
        "rangers": "nyr",    "senators": "ott",     "sens": "ott",
        "flyers": "phi",     "penguins": "pit",     "pens": "pit",
        "sharks": "sjs",     "kraken": "sea",       "blues": "stl",
        "lightning": "tbl",  "bolts": "tbl",        "leafs": "tor",
        "canucks": "van",    "knights": "vgk",      "capitals": "wsh",
        "caps": "wsh",       "jets": "wpg",
    },
    "nfl": {
        "cardinals": "ari",  "falcons": "atl",      "ravens": "bal",
        "bills": "buf",      "panthers": "car",     "bears": "chi",
        "bengals": "cin",    "browns": "cle",       "cowboys": "dal",
        "broncos": "den",    "lions": "det",        "packers": "gb",
        "texans": "hou",     "colts": "ind",        "jaguars": "jax",
        "chiefs": "kc",      "raiders": "lv",       "chargers": "lac",
        "rams": "lar",       "dolphins": "mia",     "vikings": "min",
        "patriots": "ne",    "saints": "no",        "giants": "nyg",
        "jets": "nyj",       "eagles": "phi",       "steelers": "pit",
        "49ers": "sf",       "seahawks": "sea",     "buccaneers": "tb",
        "bucs": "tb",        "titans": "ten",       "commanders": "was",
    },
    "mlb": {
        "diamondbacks": "ari", "dbacks": "ari",    "braves": "atl",
        "orioles": "bal",      "red sox": "bos",   "sox": "bos",
        "cubs": "chc",         "white sox": "cws", "reds": "cin",
        "guardians": "cle",    "rockies": "col",   "tigers": "det",
        "astros": "hou",       "royals": "kc",     "angels": "laa",
        "dodgers": "lad",      "marlins": "mia",   "brewers": "mil",
        "twins": "min",        "mets": "nym",      "yankees": "nyy",
        "athletics": "oak",    "phillies": "phi",  "pirates": "pit",
        "padres": "sd",        "giants": "sf",     "mariners": "sea",
        "cardinals": "stl",    "rays": "tb",       "rangers": "tex",
        "blue jays": "tor",    "jays": "tor",      "nationals": "was",
        "nats": "was",
    },
    "mls": {
        "united": "atl",     "fire": "chi",          "rapids": "col",
        "crew": "clb",       "dynamo": "hou",        "galaxy": "la",
        "lafc": "lafc",      "sounders": "sea",      "timbers": "por",
        "whitecaps": "van",  "union": "phi",         "revolution": "ne",
        "red bulls": "nyrb", "impact": "mtl",        "cf montreal": "mtl",
    },
    "wnba": {
        "dream": "atl",      "sky": "chi",           "sun": "conn",
        "wings": "dal",      "valkyries": "gsv",     "fever": "ind",
        "aces": "lva",       "sparks": "la",         "lynx": "min",
        "liberty": "ny",     "mercury": "phx",       "storm": "sea",
        "mystics": "was",
    },
}

# Build a word-level index: sport → {single_word → abbr}
# Handles multi-word nicknames (e.g. "blue jays") by also indexing each word.
_WORD_INDEX: dict[str, dict[str, str]] = {}
for _sport, _nicks in _SPORT_NICKS.items():
    _WORD_INDEX[_sport] = {}
    for _name, _abbr in _nicks.items():
        # Index each individual word of the nickname
        for _word in _name.split():
            if len(_word) >= 3:
                _WORD_INDEX[_sport].setdefault(_word, _abbr)


def _lookup_abbr(sport: str, label: str) -> str | None:
    """Map a Polymarket outcome label to its Action Network abbreviation.

    Tries: exact nick match → word-by-word match.
    Returns None if no mapping found.
    """
    if not label or not sport:
        return None
    label_lower = label.lower().strip()
    nicks = _SPORT_NICKS.get(sport, {})
    words = _WORD_INDEX.get(sport, {})

    if label_lower in nicks:
        return nicks[label_lower]
    for word in label_lower.split():
        if word in words:
            return words[word]
    return None


def _resolve_sport_and_abbrs(
    question: str,
    yes_lbl: str,
    no_lbl: str,
) -> tuple[str, str, str] | None:
    """Try to identify (sport, yes_abbr, no_abbr) for a Polymarket market.

    Returns (sport, yes_abbr, no_abbr) or None if resolution fails or the
    question is a spread/total market (not a simple moneyline game-winner).

    Pass 0: reject questions that contain spread/total language (e.g. "cover",
            "handicap", "+7.5", "total") — those must NOT share the same
            parent_event_id as h2h moneyline contracts.
    Pass 1: detect sport from the question text (explicit keyword like "NBA").
    Pass 2: if no sport keyword, try all sports — accept if both outcomes
            map unambiguously in exactly one sport.
    """
    # Pass 0: reject non-moneyline questions to prevent spread/moneyline mixing
    if _SPREAD_RE.search(question):
        return None
    # Pass 1: explicit sport keyword in the question
    m = _SPORT_RE.search(question)
    if m:
        sport = m.group(1).lower()
        yes_abbr = _lookup_abbr(sport, yes_lbl)
        no_abbr  = _lookup_abbr(sport, no_lbl)
        if yes_abbr and no_abbr:
            return sport, yes_abbr, no_abbr

    # Pass 2: infer sport from outcome labels when no keyword present
    matches: list[tuple[str, str, str]] = []
    for sport in _SPORT_NICKS:
        ya = _lookup_abbr(sport, yes_lbl)
        na = _lookup_abbr(sport, no_lbl)
        if ya and na:
            matches.append((sport, ya, na))

    # Only use inferred sport if unambiguous (exactly one sport matched)
    if len(matches) == 1:
        return matches[0]

    return None


class PolymarketScanner(BaseScanner):
    platform = Platform.POLYMARKET
    _min_request_interval = 2.0

    async def fetch_markets(self) -> list[MarketContract]:
        contracts: list[MarketContract] = []
        offset = 0
        limit  = 100
        pages_fetched = 0

        while pages_fetched < MAX_PAGES:
            resp = await self._throttled_get(
                f"{GAMMA_BASE}/markets",
                params={
                    "limit":        limit,
                    "offset":       offset,
                    "active":       "true",
                    "closed":       "false",
                    "order":        "volume24hr",
                    "ascending":    "false",
                },
            )
            markets = resp.json()
            if not markets:
                break

            for m in markets:
                outcome_prices = m.get("outcomePrices") or []
                outcomes       = m.get("outcomes") or []

                # Both fields may come as JSON-encoded string lists
                if isinstance(outcome_prices, str):
                    try:
                        outcome_prices = json.loads(outcome_prices)
                    except Exception:
                        continue
                if isinstance(outcomes, str):
                    try:
                        outcomes = json.loads(outcomes)
                    except Exception:
                        continue

                if len(outcomes) != 2 or len(outcome_prices) != 2:
                    continue

                try:
                    yes_price = float(outcome_prices[0])
                    no_price  = float(outcome_prices[1])
                except (ValueError, TypeError):
                    continue

                if yes_price <= 0 or no_price <= 0:
                    continue

                question = m.get("question", str(m["id"]))
                slug     = m.get("slug", m["id"])
                url      = f"https://polymarket.com/event/{slug}"
                volume   = float(m.get("volumeNum") or 0)

                # Use specific outcome names when available (e.g. "Heat", "Hawks")
                raw_yes_lbl = outcomes[0] if outcomes[0] not in ("Yes", "No", "") else "Yes"
                raw_no_lbl  = outcomes[1] if outcomes[1] not in ("Yes", "No", "") else "No"

                # ── Namespace alignment ────────────────────────────────────────
                # Try to map outcome labels to AN abbreviations and build the
                # same "{sport} {abbr_a} {abbr_b}" parent_event_id used by
                # DraftKings and Kalshi.  If that fails, fall back to the
                # question text (may still fuzzy-match non-sports markets).
                resolved = _resolve_sport_and_abbrs(question, raw_yes_lbl, raw_no_lbl)
                if resolved:
                    sport, yes_abbr, no_abbr = resolved
                    parent_event_id = normalize_event_key(
                        f"{sport} {yes_abbr} {no_abbr}"
                    )
                    # Use uppercase abbreviation as outcome_label so the matcher
                    # compares "OKC" with "OKC" (not "Thunder" with "OKC").
                    yes_lbl = yes_abbr.upper()
                    no_lbl  = no_abbr.upper()
                else:
                    parent_event_id = normalize_event_key(question)
                    yes_lbl = raw_yes_lbl
                    no_lbl  = raw_no_lbl

                for side, price, outcome_lbl, is_yes in [
                    (ContractSide.YES, yes_price, yes_lbl, True),
                    (ContractSide.NO,  no_price,  no_lbl,  False),
                ]:
                    contracts.append(MarketContract(
                        platform=self.platform,
                        # Same market_id for YES and NO — the matcher treats
                        # them as complementary sides of one binary market.
                        market_id=str(m["id"]),
                        parent_event_id=parent_event_id,
                        parent_event_title=question,
                        outcome_label=outcome_lbl,
                        is_yes_side=is_yes,
                        event_title=question,
                        side=side,
                        price=price,
                        payout_per_contract=PAYOUT,
                        decimal_odds=PAYOUT / price,
                        market_type="prediction",
                        volume_24h=volume,
                        url=url,
                        raw=m,
                    ))

            offset += limit
            pages_fetched += 1
            if len(markets) < limit:
                break

        return contracts
