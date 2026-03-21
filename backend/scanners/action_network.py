"""
Shared Action Network sportsbook scanner base class.

Action Network aggregates live moneyline odds from US sportsbooks and exposes them
via an undocumented public REST API at api.actionnetwork.com/web/v1/scoreboard.

Each sportsbook is identified by a numeric `book_id`:
  68  — DraftKings NJ   (real DK lines)
  69  — FanDuel NJ      (real FD lines, widest coverage)
  123 — Caesars NJ      (real Caesars lines)

The response always includes consensus (id=15) and open (id=30) entries regardless
of which bookIds are requested, so odds entries MUST be filtered by book_id.

Only `type="game"` odds are used (full-game moneyline). Half/quarter odds are skipped.

Games with status != "scheduled" (i.e. "inprogress" or "complete") are skipped.

Odds freshness validation:
  Action Network always returns a consensus entry (book_id=15) alongside each
  book's odds.  If the book's implied probability deviates from consensus by
  more than AN_MAX_CONSENSUS_DEVIATION (default 8%), the entry is silently
  dropped.  This eliminates phantom arb signals caused by stale cached quotes
  while passing through genuine opportunities (real ~2% edges correspond to
  only ~1-2% implied-probability deviation from consensus).

parent_event_id uses the same sport-namespaced format as DraftKings/Kalshi:
  normalize_event_key("{sport} {home_abbr} {away_abbr}")
  → "nba bos den"  — matches Kalshi's SERIES_TO_SPORT-prefixed event key.

Soccer sports (SOCCER_SPORTS) produce up to 3 contracts per game:
  home win (outcome_label=home_abbr), draw (outcome_label="TIE"), away win.
  All get num_outcomes=3 so the matcher applies 3-way complementarity logic.
"""
from __future__ import annotations
import logging
from abc import abstractmethod

from scanners.base import BaseScanner
from arbitrage.matcher import normalize_event_key
from models import MarketContract, ContractSide

logger = logging.getLogger(__name__)

AN_BASE = "https://api.actionnetwork.com/web/v1/scoreboard/{sport}?periods=full"

AN_SPORTS = [
    # Major US leagues
    "nba", "nfl", "mlb", "nhl", "wnba",
    # College
    "ncaab", "ncaaf", "ncaaw",
    # Soccer
    "mls", "soccer", "epl", "ligue1", "bundesliga", "seriea", "laliga",
    # Combat sports & tennis
    "ufc", "boxing", "tennis",
]

# Soccer leagues use 3-way markets (Home / Draw / Away) on Kalshi.
# Contracts from these sports get num_outcomes=3 so the matcher can enforce
# true 3-way complementarity instead of assuming binary exclusivity.
SOCCER_SPORTS: frozenset[str] = frozenset({
    "mls", "soccer", "epl", "ligue1", "bundesliga", "seriea", "laliga",
})

# Sports that use the "competitions" response schema instead of "games".
# Competition entries have:
#   competitors[].side ("home"|"away") and competitors[].player.{full_name, abbr}
#   odds[].type == "competition"  (not "game")
# All other sports use the standard "games" schema.
COMPETITION_SPORTS: frozenset[str] = frozenset({"tennis"})

AN_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Referer": "https://www.actionnetwork.com/",
}

# Action Network always returns a consensus entry (book_id=15) alongside the
# specific book's odds. If a book's implied probability deviates from consensus
# by more than this threshold, the odds are considered stale/unreliable and are
# skipped. Real ~2% arb edges correspond to only ~1-2% implied-prob deviation;
# stale/bad data typically causes 10-20%+ deviations.
AN_MAX_CONSENSUS_DEVIATION: float = 0.08   # 8 percentage-point gap


def _normalize_competition(comp: dict) -> dict:
    """
    Convert a competition-schema entry (tennis, golf) into the standard games
    schema so the main parsing loop can treat all sports identically.

    Competition schema:
      { "id": ..., "status": "scheduled",
        "competitors": [
          {"side": "home", "player": {"full_name": "...", "abbr": "STE", ...}},
          {"side": "away", "player": {"full_name": "...", "abbr": "SINNI", ...}},
        ],
        "odds": [{"type": "competition", "book_id": 15, "ml_home": -138, ...}]
      }

    Normalised output mirrors the games schema:
      { "id": ..., "status": "scheduled",
        "home_team_id": "home", "away_team_id": "away",
        "teams": [
          {"id": "home", "full_name": "...", "abbr": "STE"},
          {"id": "away", "full_name": "...", "abbr": "SINNI"},
        ],
        "odds": [{"type": "game", "book_id": 15, "ml_home": -138, ...}]
      }
    """
    competitors = comp.get("competitors", [])
    home_player = next(
        (c.get("player", {}) for c in competitors if c.get("side") == "home"),
        competitors[0].get("player", {}) if competitors else {},
    )
    away_player = next(
        (c.get("player", {}) for c in competitors if c.get("side") == "away"),
        competitors[1].get("player", {}) if len(competitors) > 1 else {},
    )
    # Re-tag odds type so the main loop's `type != "game"` filter passes them through
    normalised_odds = [
        {**o, "type": "game"} if o.get("type") == "competition" else o
        for o in comp.get("odds", [])
    ]
    return {
        "id":           comp.get("id"),
        "status":       comp.get("status"),
        "home_team_id": "home",
        "away_team_id": "away",
        "teams": [
            {**home_player, "id": "home"},
            {**away_player, "id": "away"},
        ],
        "odds": normalised_odds,
    }


def american_to_decimal(american: int | float) -> float:
    """Convert American moneyline odds to decimal odds (stake included in payout)."""
    a = float(american)
    if a > 0:
        return (a / 100.0) + 1.0
    return (100.0 / abs(a)) + 1.0


def _implied_prob(american: int | float) -> float:
    """Implied win probability from American odds."""
    return 1.0 / american_to_decimal(american)


def _consensus_ok(book_ml: int, consensus_ml: int) -> bool:
    """
    Return True iff the book's implied probability is within
    AN_MAX_CONSENSUS_DEVIATION of the consensus implied probability.

    If the gap is larger the odds are likely stale/bad data and should be
    dropped to avoid phantom arbitrage opportunities.
    """
    return abs(_implied_prob(book_ml) - _implied_prob(consensus_ml)) <= AN_MAX_CONSENSUS_DEVIATION


class ActionNetworkScanner(BaseScanner):
    """
    Abstract base for Action Network sportsbook scanners.

    Subclasses must set:
      platform: Platform   — e.g. Platform.DRAFTKINGS
      book_id:  int        — e.g. 68 for DraftKings NJ

    Fetches full-game moneyline odds for all AN_SPORTS and produces:
      • 2 contracts per binary-sport game (home win, away win)
      • up to 3 contracts per soccer game (home win, draw, away win)

    Contracts are sport-namespaced so parent_event_ids match Kalshi Phase-1.
    Only "scheduled" games are included — live/finished games are skipped.
    """

    book_id: int  # set in subclass
    _min_request_interval = 3.0

    async def fetch_markets(self) -> list[MarketContract]:
        contracts: list[MarketContract] = []
        skipped_live = 0

        for sport in AN_SPORTS:
            is_soccer = sport in SOCCER_SPORTS
            url = f"{AN_BASE.format(sport=sport)}&bookIds={self.book_id}"
            try:
                resp = await self._throttled_get(url, headers=AN_HEADERS)
                data = resp.json()
            except Exception as exc:
                logger.warning(f"[{self.platform.value}] {sport} endpoint failed: {exc}")
                continue

            try:
                # Competition sports (tennis) use a different top-level key and
                # schema.  Normalise them to the standard games shape so the rest
                # of the loop is identical for all sports.
                if sport in COMPETITION_SPORTS:
                    games = [_normalize_competition(c) for c in data.get("competitions", [])]
                else:
                    games = data.get("games", [])
                for game in games:
                    # Only process upcoming (scheduled) games.
                    # "inprogress" and "complete" games have unactionable odds.
                    game_status = game.get("status", "").lower()
                    if game_status != "scheduled":
                        skipped_live += 1
                        continue

                    teams = game.get("teams", [])
                    if len(teams) < 2:
                        continue

                    home_team_id = game.get("home_team_id")
                    away_team_id = game.get("away_team_id")

                    teams_by_id = {t.get("id"): t for t in teams}
                    home_team = teams_by_id.get(home_team_id, teams[0])
                    away_team = teams_by_id.get(away_team_id, teams[1])

                    home_name  = home_team.get("full_name", home_team.get("abbr", "Home"))
                    away_name  = away_team.get("full_name", away_team.get("abbr", "Away"))
                    home_abbr  = home_team.get("abbr") or home_team.get("short_name") or home_name
                    away_abbr  = away_team.get("abbr") or away_team.get("short_name") or away_name

                    game_title = f"{away_name} @ {home_name}"
                    game_id    = str(game.get("id", ""))

                    # Sport-namespaced parent_event_id matches Kalshi exactly:
                    # normalize_event_key("nba BOS DEN") → "bos den nba"
                    parent_event_id = normalize_event_key(f"{sport} {home_abbr} {away_abbr}")

                    # Collect both book-specific and consensus (id=15) odds in
                    # one pass. Consensus is always present in the AN response
                    # and is used to validate that the book's odds are fresh.
                    ml_home: int | None = None
                    ml_away: int | None = None
                    ml_draw: int | None = None
                    con_home: int | None = None  # consensus ml_home
                    con_away: int | None = None  # consensus ml_away

                    for odds_entry in game.get("odds", []):
                        if odds_entry.get("type") != "game":
                            continue
                        bid = odds_entry.get("book_id")
                        if bid == self.book_id:
                            ml_home = odds_entry.get("ml_home")
                            ml_away = odds_entry.get("ml_away")
                            if is_soccer:
                                ml_draw = odds_entry.get("ml_draw")
                        elif bid == 15:  # Action Network consensus
                            con_home = odds_entry.get("ml_home")
                            con_away = odds_entry.get("ml_away")

                    if ml_home is None or ml_away is None:
                        continue

                    # Validate odds freshness against consensus.
                    # Skip if the book's implied probability deviates too far —
                    # this is the primary cause of phantom arb signals.
                    if con_home is not None and not _consensus_ok(ml_home, con_home):
                        logger.debug(
                            f"[{self.platform.value}] {game_title} home odds {ml_home} "
                            f"deviate from consensus {con_home} — skipping stale data."
                        )
                        continue
                    if con_away is not None and not _consensus_ok(ml_away, con_away):
                        logger.debug(
                            f"[{self.platform.value}] {game_title} away odds {ml_away} "
                            f"deviate from consensus {con_away} — skipping stale data."
                        )
                        continue

                    try:
                        dec_home = american_to_decimal(int(ml_home))
                        dec_away = american_to_decimal(int(ml_away))
                    except (ValueError, TypeError):
                        continue

                    platform_tag = self.platform.value.capitalize()
                    # Soccer markets have 3 possible outcomes; binary sports have 2.
                    n_outcomes = 3 if is_soccer else 2

                    # Home team win contract
                    contracts.append(MarketContract(
                        platform=self.platform,
                        market_id=f"{game_id}_home",
                        parent_event_id=parent_event_id,
                        parent_event_title=game_title,
                        outcome_label=home_abbr,
                        is_yes_side=True,
                        event_title=f"{game_title} — {home_name} to win [{platform_tag}]",
                        side=ContractSide.YES,
                        price=1.0 / dec_home,
                        payout_per_contract=1.0,
                        decimal_odds=dec_home,
                        num_outcomes=n_outcomes,
                        raw={
                            "team": home_name, "abbr": home_abbr,
                            "american_odds": ml_home, "decimal_odds": dec_home,
                            "book_id": self.book_id,
                        },
                    ))

                    # Away team win contract
                    contracts.append(MarketContract(
                        platform=self.platform,
                        market_id=f"{game_id}_away",
                        parent_event_id=parent_event_id,
                        parent_event_title=game_title,
                        outcome_label=away_abbr,
                        is_yes_side=False,
                        event_title=f"{game_title} — {away_name} to win [{platform_tag}]",
                        side=ContractSide.NO,
                        price=1.0 / dec_away,
                        payout_per_contract=1.0,
                        decimal_odds=dec_away,
                        num_outcomes=n_outcomes,
                        raw={
                            "team": away_name, "abbr": away_abbr,
                            "american_odds": ml_away, "decimal_odds": dec_away,
                            "book_id": self.book_id,
                        },
                    ))

                    # Draw contract (soccer only — when draw odds are available)
                    if is_soccer and ml_draw is not None:
                        try:
                            dec_draw = american_to_decimal(int(ml_draw))
                        except (ValueError, TypeError):
                            pass
                        else:
                            contracts.append(MarketContract(
                                platform=self.platform,
                                market_id=f"{game_id}_draw",
                                parent_event_id=parent_event_id,
                                parent_event_title=game_title,
                                outcome_label="TIE",
                                is_yes_side=True,
                                event_title=f"{game_title} — Draw [{platform_tag}]",
                                side=ContractSide.YES,
                                price=1.0 / dec_draw,
                                payout_per_contract=1.0,
                                decimal_odds=dec_draw,
                                num_outcomes=3,
                                raw={
                                    "team": "Draw", "abbr": "TIE",
                                    "american_odds": ml_draw, "decimal_odds": dec_draw,
                                    "book_id": self.book_id,
                                },
                            ))

            except Exception as exc:
                logger.warning(f"[{self.platform.value}] parse error on {sport}: {exc}")
                continue

        logger.info(
            f"[{self.platform.value}] {len(contracts)} contracts via Action Network "
            f"book_id={self.book_id} ({skipped_live} live/finished games skipped)."
        )
        return contracts
