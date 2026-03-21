"""
The Odds API — free tier (500 req/month). Key required.
https://api.the-odds-api.com/v4/sports/
Converts American moneyline odds to decimal odds.

Data structure:
  - Each event has home_team and away_team — mutually exclusive outcomes.
  - Multiple bookmakers may have odds for the same event.
  - We emit one contract per team per whitelisted bookmaker, tagged with
    the correct Platform (FanDuel, BetMGM, Caesars).
  - DraftKings is excluded — covered by the dedicated DraftKingsScanner
    which uses Action Network and includes the home/away ID fix.
  - parent_event_id groups all outcomes of the same game for cross-platform
    matching against Polymarket / Kalshi contracts.

Namespace alignment (critical for oracle matching):
  Action Network (DK/FD/Caesars) and Kalshi use abbreviation-based event IDs:
    normalize_event_key("nba OKC TOR") → "nba okc tor"
  The Odds API returns full team names ("Oklahoma City Thunder vs Toronto Raptors"),
  which after normalization produce a completely different key ("city oklahoma ...").
  SPORT_KEY_MAP and TEAM_ABBR_MAP translate Odds API full names to the same
  abbreviation-based namespace for h2h contracts, enabling exact-bucket matching
  with DK/Kalshi in the fuzzy matcher and populating sb_oracle_map / kalshi_oracle_map.
  Spreads use abs(point) in the parent_event_id so both sides of a line share
  the same bucket (e.g. "OKC -5.5" and "TOR +5.5" are both keyed by "5.5").

Sport discovery:
  The /v4/sports endpoint lists all currently active sports WITHOUT consuming
  API quota. We fetch it dynamically each scan so new leagues appear automatically.
  A hardcoded fallback list is used if that fetch fails.
"""
from __future__ import annotations
import logging

from scanners.base import BaseScanner
from arbitrage.matcher import normalize_event_key
from models import MarketContract, Platform, ContractSide
from config import settings

logger = logging.getLogger(__name__)

BASE_URL = "https://api.the-odds-api.com/v4"

# Map Odds API bookmaker key → our Platform enum.
# Only whitelisted books are emitted; all others are silently skipped.
# DraftKings omitted — covered by DraftKingsScanner (Action Network).
# Pinnacle omitted — covered by PinnacleScanner (Action Network, book_id=3).
#   Using Action Network for Pinnacle guarantees the same abbreviation-based
#   parent_event_id namespace as DK/Kalshi, enabling exact-bucket oracle matching.
# BetMGM is the primary addition here — not available via Action Network.
BOOKMAKER_PLATFORM_MAP: dict[str, Platform] = {
    "fanduel":         Platform.FANDUEL,
    "betmgm":          Platform.BETMGM,
    "caesars":         Platform.CAESARS,
    "williamhill_us":  Platform.CAESARS,   # William Hill US = Caesars-branded
}

# Comprehensive fallback sport list used if the dynamic /sports fetch fails.
# Covers all major leagues Polymarket/Kalshi also list events for.
FALLBACK_SPORTS = [
    # US major leagues
    "americanfootball_nfl",
    "basketball_nba",
    "baseball_mlb",
    "icehockey_nhl",
    "basketball_wnba",
    # College
    "americanfootball_ncaaf",
    "basketball_ncaab",
    # Soccer — top leagues
    "soccer_usa_mls",
    "soccer_epl",
    "soccer_france_ligue_1",
    "soccer_germany_bundesliga",
    "soccer_italy_serie_a",
    "soccer_spain_la_liga",
    "soccer_uefa_champs_league",
    "soccer_uefa_europa_league",
    # Combat sports
    "mma_mixed_martial_arts",
    "boxing_boxing",
    # Tennis grand slams
    "tennis_atp_wimbledon",
    "tennis_atp_us_open",
    "tennis_atp_french_open",
    "tennis_atp_aus_open",
    "tennis_wta_wimbledon",
    "tennis_wta_us_open",
    "tennis_wta_french_open",
    "tennis_wta_aus_open",
    # Golf
    "golf_masters_tournament_winner",
    "golf_pga_championship_winner",
    "golf_us_open_winner",
    "golf_the_open_championship_winner",
]


# Maps Odds API sport key → Action Network sport abbreviation used in DK/Kalshi parent_event_ids.
# Enables Pinnacle h2h contracts to share the same namespace as DK/Kalshi after normalization.
SPORT_KEY_MAP: dict[str, str] = {
    "basketball_nba":                    "nba",
    "americanfootball_nfl":              "nfl",
    "baseball_mlb":                      "mlb",
    "icehockey_nhl":                     "nhl",
    "basketball_wnba":                   "wnba",
    "americanfootball_ncaaf":            "ncaaf",
    "basketball_ncaab":                  "ncaab",
    "basketball_ncaaw":                  "ncaaw",
    "soccer_usa_mls":                    "mls",
    "soccer_epl":                        "epl",
    "soccer_france_ligue_1":             "ligue1",
    "soccer_germany_bundesliga":         "bundesliga",
    "soccer_italy_serie_a":              "seriea",
    "soccer_spain_la_liga":              "laliga",
    "soccer_uefa_champs_league":         "soccer",
    "soccer_uefa_europa_league":         "soccer",
    "mma_mixed_martial_arts":            "ufc",
    "boxing_boxing":                     "boxing",
    "tennis_atp_wimbledon":              "tennis",
    "tennis_atp_us_open":                "tennis",
    "tennis_atp_french_open":            "tennis",
    "tennis_atp_aus_open":               "tennis",
    "tennis_wta_wimbledon":              "tennis",
    "tennis_wta_us_open":                "tennis",
    "tennis_wta_french_open":            "tennis",
    "tennis_wta_aus_open":               "tennis",
    "golf_masters_tournament_winner":    "golf",
    "golf_pga_championship_winner":      "golf",
    "golf_us_open_winner":               "golf",
    "golf_the_open_championship_winner": "golf",
}

# Maps lowercase Odds API full team name → lowercase abbreviation used by Kalshi/Action Network.
# Covers all major US sports leagues. Teams absent from this map fall back to the full-name
# parent_event_id (no cross-platform oracle matching for those teams, but no breakage either).
TEAM_ABBR_MAP: dict[str, str] = {
    # ── NBA ──────────────────────────────────────────────────────────────────────
    "atlanta hawks":              "atl",
    "boston celtics":             "bos",
    "brooklyn nets":              "bkn",
    "charlotte hornets":          "cha",
    "chicago bulls":              "chi",
    "cleveland cavaliers":        "cle",
    "dallas mavericks":           "dal",
    "denver nuggets":             "den",
    "detroit pistons":            "det",
    "golden state warriors":      "gsw",
    "houston rockets":            "hou",
    "indiana pacers":             "ind",
    "la clippers":                "lac",
    "los angeles clippers":       "lac",
    "la lakers":                  "lal",
    "los angeles lakers":         "lal",
    "memphis grizzlies":          "mem",
    "miami heat":                 "mia",
    "milwaukee bucks":            "mil",
    "minnesota timberwolves":     "min",
    "new orleans pelicans":       "nop",
    "new york knicks":            "nyk",
    "oklahoma city thunder":      "okc",
    "orlando magic":              "orl",
    "philadelphia 76ers":         "phi",
    "phoenix suns":               "phx",
    "portland trail blazers":     "por",
    "sacramento kings":           "sac",
    "san antonio spurs":          "sas",
    "toronto raptors":            "tor",
    "utah jazz":                  "uta",
    "washington wizards":         "was",
    # ── NFL ──────────────────────────────────────────────────────────────────────
    "arizona cardinals":          "ari",
    "atlanta falcons":            "atl",
    "baltimore ravens":           "bal",
    "buffalo bills":              "buf",
    "carolina panthers":          "car",
    "chicago bears":              "chi",
    "cincinnati bengals":         "cin",
    "cleveland browns":           "cle",
    "dallas cowboys":             "dal",
    "denver broncos":             "den",
    "detroit lions":              "det",
    "green bay packers":          "gb",
    "houston texans":             "hou",
    "indianapolis colts":         "ind",
    "jacksonville jaguars":       "jax",
    "kansas city chiefs":         "kc",
    "las vegas raiders":          "lv",
    "los angeles chargers":       "lac",
    "los angeles rams":           "lar",
    "miami dolphins":             "mia",
    "minnesota vikings":          "min",
    "new england patriots":       "ne",
    "new orleans saints":         "no",
    "new york giants":            "nyg",
    "new york jets":              "nyj",
    "philadelphia eagles":        "phi",
    "pittsburgh steelers":        "pit",
    "san francisco 49ers":        "sf",
    "seattle seahawks":           "sea",
    "tampa bay buccaneers":       "tb",
    "tennessee titans":           "ten",
    "washington commanders":      "was",
    # ── MLB ──────────────────────────────────────────────────────────────────────
    "arizona diamondbacks":       "ari",
    "atlanta braves":             "atl",
    "baltimore orioles":          "bal",
    "boston red sox":             "bos",
    "chicago cubs":               "chc",
    "chicago white sox":          "cws",
    "cincinnati reds":            "cin",
    "cleveland guardians":        "cle",
    "colorado rockies":           "col",
    "detroit tigers":             "det",
    "houston astros":             "hou",
    "kansas city royals":         "kc",
    "los angeles angels":         "laa",
    "los angeles dodgers":        "lad",
    "miami marlins":              "mia",
    "milwaukee brewers":          "mil",
    "minnesota twins":            "min",
    "new york mets":              "nym",
    "new york yankees":           "nyy",
    "oakland athletics":          "oak",
    "athletics":                  "oak",
    "sacramento athletics":       "sac",
    "philadelphia phillies":      "phi",
    "pittsburgh pirates":         "pit",
    "san diego padres":           "sd",
    "san francisco giants":       "sf",
    "seattle mariners":           "sea",
    "st. louis cardinals":        "stl",
    "st louis cardinals":         "stl",
    "tampa bay rays":             "tb",
    "texas rangers":              "tex",
    "toronto blue jays":          "tor",
    "washington nationals":       "was",
    # ── NHL ──────────────────────────────────────────────────────────────────────
    "anaheim ducks":              "ana",
    "arizona coyotes":            "ari",
    "utah hockey club":           "uta",
    "boston bruins":              "bos",
    "buffalo sabres":             "buf",
    "calgary flames":             "cgy",
    "carolina hurricanes":        "car",
    "chicago blackhawks":         "chi",
    "colorado avalanche":         "col",
    "columbus blue jackets":      "cbj",
    "dallas stars":               "dal",
    "detroit red wings":          "det",
    "edmonton oilers":            "edm",
    "florida panthers":           "fla",
    "los angeles kings":          "lak",
    "minnesota wild":             "min",
    "montreal canadiens":         "mtl",
    "nashville predators":        "nsh",
    "new jersey devils":          "njd",
    "new york islanders":         "nyi",
    "new york rangers":           "nyr",
    "ottawa senators":            "ott",
    "philadelphia flyers":        "phi",
    "pittsburgh penguins":        "pit",
    "san jose sharks":            "sjs",
    "seattle kraken":             "sea",
    "st. louis blues":            "stl",
    "st louis blues":             "stl",
    "tampa bay lightning":        "tbl",
    "toronto maple leafs":        "tor",
    "vancouver canucks":          "van",
    "vegas golden knights":       "vgk",
    "washington capitals":        "wsh",
    "winnipeg jets":              "wpg",
    # ── WNBA ─────────────────────────────────────────────────────────────────────
    "atlanta dream":              "atl",
    "chicago sky":                "chi",
    "connecticut sun":            "conn",
    "dallas wings":               "dal",
    "golden state valkyries":     "gsv",
    "indiana fever":              "ind",
    "las vegas aces":             "lva",
    "los angeles sparks":         "la",
    "minnesota lynx":             "min",
    "new york liberty":           "ny",
    "phoenix mercury":            "phx",
    "seattle storm":              "sea",
    "washington mystics":         "was",
    # ── MLS ──────────────────────────────────────────────────────────────────────
    "atlanta united fc":          "atl",
    "atlanta united":             "atl",
    "austin fc":                  "aus",
    "cf montreal":                "mtl",
    "charlotte fc":               "clt",
    "chicago fire fc":            "chi",
    "chicago fire":               "chi",
    "fc cincinnati":              "cin",
    "colorado rapids":            "col",
    "columbus crew":              "clb",
    "d.c. united":                "dc",
    "dc united":                  "dc",
    "fc dallas":                  "dal",
    "houston dynamo fc":          "hou",
    "houston dynamo":             "hou",
    "inter miami cf":             "mia",
    "la galaxy":                  "la",
    "los angeles fc":             "lafc",
    "lafc":                       "lafc",
    "minnesota united fc":        "min",
    "minnesota united":           "min",
    "nashville sc":               "nsh",
    "new england revolution":     "ne",
    "new york city fc":           "nyc",
    "new york red bulls":         "nyrb",
    "orlando city sc":            "orl",
    "orlando city":               "orl",
    "philadelphia union":         "phi",
    "portland timbers":           "por",
    "real salt lake":             "rsl",
    "san jose earthquakes":       "sj",
    "seattle sounders fc":        "sea",
    "seattle sounders":           "sea",
    "sporting kansas city":       "skc",
    "st. louis city sc":          "stl",
    "toronto fc":                 "tor",
    "vancouver whitecaps fc":     "van",
    "vancouver whitecaps":        "van",
}


def american_to_decimal(american: int | float) -> float:
    """Convert American moneyline odds to decimal odds."""
    a = float(american)
    if a > 0:
        return (a / 100.0) + 1.0
    else:
        return (100.0 / abs(a)) + 1.0


class OddsApiScanner(BaseScanner):
    platform = Platform.ODDS_API
    _min_request_interval = 2.0  # Dynamic sport list means more calls; keep quota safe

    async def _get_active_sports(self) -> list[str]:
        """
        Fetch the list of currently in-season sports from The Odds API.
        This endpoint does NOT count against the monthly quota.
        Returns sport keys for non-futures markets only (has_outrights=False).
        Falls back to FALLBACK_SPORTS if the fetch fails.
        """
        try:
            resp = await self._throttled_get(
                f"{BASE_URL}/sports",
                params={"apiKey": settings.ODDS_API_KEY, "all": "false"},
            )
            sports = resp.json()
            # Filter out outrights/futures (e.g. "who wins the season") — we want
            # game-level moneylines only.
            active = [s["key"] for s in sports if not s.get("has_outrights", False)]
            logger.info(f"[odds_api] Discovered {len(active)} active sports from API.")
            return active
        except Exception as exc:
            logger.warning(f"[odds_api] Sports discovery failed ({exc}); using fallback list.")
            return FALLBACK_SPORTS

    async def fetch_markets(self) -> list[MarketContract]:
        if not settings.ODDS_API_KEY:
            logger.info("[odds_api] No API key configured — skipping.")
            return []

        sports = await self._get_active_sports()
        contracts: list[MarketContract] = []

        for sport in sports:
            try:
                resp = await self._throttled_get(
                    f"{BASE_URL}/sports/{sport}/odds",
                    params={
                        "apiKey":     settings.ODDS_API_KEY,
                        "regions":    "us",
                        "markets":    "h2h,spreads,totals",
                        "oddsFormat": "american",
                        # Request only our whitelisted books to reduce response size
                        "bookmakers": ",".join(BOOKMAKER_PLATFORM_MAP.keys()),
                    },
                )
                events = resp.json()
                # API returns a dict with error details when sport has no data
                if not isinstance(events, list):
                    continue
            except Exception as exc:
                import httpx as _httpx
                if isinstance(exc, _httpx.HTTPStatusError) and exc.response.status_code == 401:
                    logger.warning(
                        "[odds_api] 401 Unauthorized — API key lacks sportsbook access. "
                        "Skipping remaining sports. (Set a valid key with sportsbook permissions "
                        "to enable BetMGM/FanDuel/Caesars via The Odds API.)"
                    )
                    break  # Stop the loop — 401 will apply to all remaining sports too
                logger.warning(f"[odds_api] {sport} failed: {exc}")
                continue

            # Resolve sport abbreviation for namespace alignment with DK/Kalshi
            sport_abbr = SPORT_KEY_MAP.get(sport, "")

            for event in events:
                home = event.get("home_team", "")
                away = event.get("away_team", "")
                game_title = f"{home} vs {away}"
                event_id   = event.get("id", "")

                # Abbreviation lookup — used for h2h parent_event_id and outcome_label.
                # If found, the key matches DK/Kalshi's namespace exactly (e.g. "nba okc tor").
                # If absent, we fall back to the full-name key (cross-platform oracle matching
                # will not work for those teams, but no breakage occurs).
                home_abbr = TEAM_ABBR_MAP.get(home.lower(), "")
                away_abbr = TEAM_ABBR_MAP.get(away.lower(), "")

                # Full-name fallback (used for spreads/totals and unrecognised teams)
                parent_event_id = normalize_event_key(game_title)

                for bookmaker in event.get("bookmakers", []):
                    book_key   = bookmaker.get("key", "")
                    book_title = bookmaker.get("title", book_key)

                    # Only process whitelisted bookmakers
                    platform = BOOKMAKER_PLATFORM_MAP.get(book_key)
                    if platform is None:
                        continue

                    for market in bookmaker.get("markets", []):
                        market_key = market.get("key")
                        if market_key not in ("h2h", "spreads", "totals"):
                            continue
                        outcomes = market.get("outcomes", [])
                        if len(outcomes) != 2:
                            continue

                        for i, outcome in enumerate(outcomes):
                            team_name = outcome.get("name", "")
                            american  = outcome.get("price")
                            if american is None:
                                continue

                            dec_odds = american_to_decimal(int(american))
                            point    = outcome.get("point")  # handicap / total line (may be None for h2h)

                            # ── Outcome label and side logic per market type ──────────────
                            if market_key == "h2h":
                                # Moneyline: use abbreviation-based parent_event_id and label
                                # when available so Pinnacle contracts land in the SAME bucket
                                # as DK/Kalshi (e.g. "nba okc tor" matches DK's exact-match key).
                                is_home = (team_name == home)
                                if sport_abbr and home_abbr and away_abbr:
                                    # Abbreviation namespace — matches DK/Kalshi exactly.
                                    outcome_label    = home_abbr if is_home else away_abbr
                                    market_parent_id = normalize_event_key(
                                        f"{sport_abbr} {home_abbr} {away_abbr}"
                                    )
                                else:
                                    # Fallback: full-name key (no cross-platform oracle matching)
                                    outcome_label    = team_name
                                    market_parent_id = parent_event_id

                            elif market_key == "spreads":
                                # Spread: label = "Team ±N" e.g. "Oklahoma City Thunder -7.5"
                                # parent_event_id uses abs(point) so BOTH sides of the same line
                                # (e.g. OKC -7.5 and TOR +7.5) share the same bucket — enabling
                                # FanDuel/Pinnacle spread pair matching for the oracle pass.
                                if point is None:
                                    continue
                                abs_point = abs(point)
                                abs_str   = (f"{abs_point:.1f}" if abs_point != int(abs_point)
                                             else f"{int(abs_point)}")
                                signed_str = (f"{point:+.1f}" if point != int(point)
                                              else f"{int(point):+d}")
                                outcome_label    = f"{team_name} {signed_str}"
                                is_home          = (team_name == home)
                                market_parent_id = normalize_event_key(
                                    f"{game_title} spread {abs_str}"
                                )

                            else:  # totals
                                # Total: label = "Over 225.5" or "Under 225.5"
                                # Both sides share the same line value → same bucket naturally.
                                if point is None:
                                    continue
                                point_str     = f"{point:.1f}" if point != int(point) else f"{int(point)}"
                                outcome_label = f"{team_name} {point_str}"  # "Over 225.5" / "Under 225.5"
                                # Over is treated as YES side (first alternative), Under as NO
                                is_home       = (team_name.lower() == "over")
                                market_parent_id = normalize_event_key(
                                    f"{game_title} total {point_str}"
                                )
                            # ─────────────────────────────────────────────────────────────

                            side = ContractSide.YES if is_home else ContractSide.NO

                            _mtype = (
                                "moneyline" if market_key == "h2h"
                                else "spread" if market_key == "spreads"
                                else "total"
                            )
                            contracts.append(MarketContract(
                                platform=platform,
                                market_id=f"{event_id}_{book_key}_{market_key}_{i}",
                                parent_event_id=market_parent_id,
                                parent_event_title=game_title,
                                outcome_label=outcome_label,
                                is_yes_side=is_home,
                                event_title=f"{game_title} [{book_title}] ({market_key})",
                                side=side,
                                price=1.0 / dec_odds,
                                payout_per_contract=1.0,
                                decimal_odds=dec_odds,
                                market_type=_mtype,
                                raw={
                                    "team":          team_name,
                                    "american_odds":  american,
                                    "decimal_odds":   dec_odds,
                                    "book":           book_title,
                                    "sport":          sport,
                                    "market":         market_key,
                                    "point":          point,
                                },
                            ))

        logger.info(
            f"[odds_api] Fetched {len(contracts)} contracts across {len(sports)} sports "
            f"(h2h, spreads, totals — FanDuel, BetMGM, Caesars). "
            f"BetMGM h2h uses abbreviation-based parent_event_ids for Kalshi/DK matching."
        )
        return contracts
