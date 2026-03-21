"""
Kalshi REST API scanner — sports moneylines + binary single-market events.
Docs: https://docs.kalshi.com
Base URL: https://api.elections.kalshi.com/trade-api/v2

FETCH STRATEGY
──────────────
Phase 1 — Game series (explicit sports moneylines):
  Fetches each known game series directly via the series_ticker parameter.

  Binary games (NBA, NHL, MLB, …) have exactly 2 markets, one per team.
    → 2 contracts per game: YES (Team A wins) and NO (Team B wins).
    → outcome_label = team abbreviation ("OKC", "TOR").
    → parent_event_id = normalize_event_key("{sport} {abbr_a} {abbr_b}")

  Soccer games (MLS, EPL, etc.) have exactly 3 markets: Home / TIE / Away.
    → 6 contracts per game (YES + NO for each of the 3 markets).
    → YES contracts: outcome_label = abbr ("SD", "TIE", "STL")
    → NO  contracts: outcome_label = "not_" + abbr ("not_SD", "not_TIE", "not_STL")
    → The "not_X" label signals the matcher that this pays when X does NOT win,
      which is the complement needed for a hedge against "X wins" at a sportsbook.
    → num_outcomes=3 so the matcher enforces strict 3-way complementarity:
      only "X" ↔ "not_X" pairings are valid hedges (never "SD" ↔ "TIE").
    → parent_event_id = normalize_event_key("{sport} {team_a} {team_b}") (no TIE)

  The sport slug (from SERIES_TO_SPORT) is prepended to the parent_event_id
  so abbreviations shared across sports ("PHI" = 76ers/Phillies/Flyers) cannot
  produce false cross-sport matches.

  Live-game guard: games whose close_time is within 30 minutes of now are
  skipped — both legs of an arb must be placeable before market close.

Phase 2 — General pagination (binary single-market events):
  Fetches all events, only processes single-market (YES/NO) events.
  These are political, economic, and miscellaneous events.
  Game prop events (format "Team A at Team B: Prop Name") are skipped to
  avoid false cross-platform matches with DraftKings moneylines.

API facts:
  - Prices are in cents (0–99). Payout = $1.00.
  - Game events: subtitle is ALWAYS empty. Team info comes from ticker suffix only.
  - Game event titles use "at" (not "vs"/"@"): "Oklahoma City at Toronto".
"""
from __future__ import annotations
import logging
import re
from datetime import datetime, timedelta, timezone

from scanners.base import BaseScanner
from arbitrage.matcher import normalize_event_key
from models import MarketContract, Platform, ContractSide

logger = logging.getLogger(__name__)

BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
PAYOUT = 1.0

# ── Phase 1: Known game series ─────────────────────────────────────────────────
# The scanner fetches each series explicitly via the series_ticker parameter.
# Series that don't exist or have no current events return 0 results (handled gracefully).
#
# SERIES_TO_SPORT maps each Kalshi series to the Action Network sport slug used by
# DraftKings. Both scanners include the sport slug in their parent_event_id to
# prevent abbreviation collisions across leagues (e.g. "PHI" = 76ers/Phillies/Flyers).
GAME_SERIES = [
    "KXNBAGAME",      # NBA basketball (active Feb–Jun)
    "KXNHLGAME",      # NHL hockey (active Oct–Jun)
    "KXMLBGAME",      # MLB baseball (active Mar–Oct)
    "KXNFLGAME",      # NFL football (active Sep–Feb)
    "KXNCAAMBGAME",   # NCAA Men's Basketball (active Nov–Apr)
    "KXNCAAFGAME",    # NCAA Football (active Aug–Jan)
    "KXWNBAGAME",     # WNBA (active May–Oct)
    "KXMLSGAME",      # MLS Soccer (active Feb–Nov)
    "KXEPLGAME",      # English Premier League
    "KXBUNDGAME",     # Bundesliga
    "KXSERIEAGAME",   # Serie A
    "KXLALIGGAME",    # La Liga
    "KXLIGUE1GAME",   # Ligue 1
    "KXUFCFIGHT",     # UFC/MMA
    "KXBOXINGFIGHT",  # Boxing
    "KXTENNISGAME",   # Tennis
    "KXNCAAWBGAME",   # NCAA Women's Basketball (active Nov–Apr)
]

# Maps Kalshi series ticker → Action Network / DraftKings sport slug.
# The slug is prepended to parent_event_id in both scanners so "nba mia phi" ≠ "mlb mia phi".
SERIES_TO_SPORT: dict[str, str] = {
    "KXNBAGAME":     "nba",
    "KXNHLGAME":     "nhl",
    "KXMLBGAME":     "mlb",
    "KXNFLGAME":     "nfl",
    "KXNCAAMBGAME":  "ncaab",
    "KXNCAAFGAME":   "ncaaf",
    "KXWNBAGAME":    "wnba",
    "KXMLSGAME":     "mls",
    "KXEPLGAME":     "epl",
    "KXBUNDGAME":    "bundesliga",
    "KXSERIEAGAME":  "seriea",
    "KXLALIGGAME":   "laliga",
    "KXLIGUE1GAME":  "ligue1",
    "KXUFCFIGHT":    "ufc",
    "KXBOXINGFIGHT": "boxing",
    "KXTENNISGAME":  "tennis",
    "KXNCAAWBGAME":  "ncaaw",
}

# Phase 2 pagination limits
MAX_EVENT_PAGES              = 20   # 20 pages × 1 s/req ≈ 20 s max for Phase 2
NEAR_TERM_DAYS               = 60   # include events closing within 60 days
CONSECUTIVE_EMPTY_PAGES_LIMIT = 5


# ── Helper functions ───────────────────────────────────────────────────────────

def _extract_abbr_from_ticker(ticker: str) -> str | None:
    """
    Extract the team/player abbreviation from a Kalshi market ticker.
    The abbreviation is the last segment after the final '-'.

    Examples:
      "KXNBAGAME-26FEB26OKCTOR-OKC" -> "OKC"
      "KXNHLGAME-26FEB26EDMLA-EDM"  -> "EDM"

    Returns None if the suffix doesn't look like a valid abbreviation
    (must be 2–6 uppercase letters, no digits).
    """
    if "-" not in ticker:
        return None
    suffix = ticker.rsplit("-", 1)[-1].strip()
    if not suffix:
        return None
    # Valid abbreviation: 2–6 uppercase letters only (no digits, no special chars)
    if not re.match(r"^[A-Z]{2,6}$", suffix):
        return None
    return suffix


def _is_prop_title(title: str) -> bool:
    """
    Return True if the title looks like a game prop rather than a moneyline.
    Pattern: "Team A at/vs/@ Team B: Prop Name" — the colon appears AFTER
    the game matchup separator.

    Examples:
      "Montreal at Chicago Fire: Both Teams to Score" → True  (prop)
      "Oklahoma City at Toronto"                     → False (game winner)
      "UFC 310: Fighter A vs Fighter B"              → False (colon before 'vs')
    """
    parts = re.split(r"\s+(?:vs\.?|@|at)\s+", title, maxsplit=1, flags=re.IGNORECASE)
    return len(parts) >= 2 and ":" in parts[1]


def _parse_close_time(market: dict) -> datetime | None:
    """Parse the close_time field from a Kalshi market dict."""
    raw_ct = market.get("close_time")
    if not raw_ct:
        return None
    try:
        return datetime.fromisoformat(raw_ct.replace("Z", "+00:00"))
    except ValueError:
        return None


# ── Scanner class ──────────────────────────────────────────────────────────────

class KalshiScanner(BaseScanner):
    platform = Platform.KALSHI
    _min_request_interval = 0.8

    def _parse_game_events(
        self,
        events: list[dict],
        cutoff: datetime,
        seen_keys: set[tuple[str, str]],
        sport: str = "",
        now: datetime | None = None,
    ) -> list[MarketContract]:
        """
        Parse game-series events into MarketContracts.

        Handles two event structures:
          • 2-market binary (NBA, NHL, MLB, …): one market per team.
            Produces 2 contracts — YES (team A wins) and NO (team B wins).
          • 3-market soccer (MLS, EPL, …): Home / TIE / Away markets.
            Produces 6 contracts — YES and NO for each market, where the
            NO contracts carry "not_" prefixed labels (e.g. "not_SD") so
            the matcher can identify true 3-way complements.

        Live-game guard: events whose close_time is within LIVE_BUFFER of now
        are skipped (both hedge legs must be executable before market close).

        sport: Action Network slug prepended to parent_event_id to prevent
               cross-sport abbreviation collisions ("PHI" = 76ers/Phillies/Flyers).
        """
        LIVE_BUFFER = timedelta(minutes=30)
        _now = now or datetime.now(timezone.utc)
        contracts: list[MarketContract] = []
        prefix = f"{sport} " if sport else ""

        for event in events:
            event_title = event.get("title", "")
            markets     = event.get("markets", [])

            if not event_title or not markets:
                continue

            # Collect (market_dict, abbreviation) for each market with a valid abbr
            abbr_pairs: list[tuple[dict, str]] = []
            for m in markets:
                abbr = _extract_abbr_from_ticker(m.get("ticker", ""))
                if abbr:
                    abbr_pairs.append((m, abbr))

            if len(abbr_pairs) == 2:
                # ── Binary game (NBA, NHL, MLB, NFL, …) ───────────────────────
                m1, abbr_a = abbr_pairs[0]
                _,  abbr_b = abbr_pairs[1]

                yes_ask = m1.get("yes_ask_dollars")
                no_ask  = m1.get("no_ask_dollars")
                if yes_ask is None or no_ask is None:
                    continue
                yes_ask = float(yes_ask)
                no_ask  = float(no_ask)
                if yes_ask <= 0 or no_ask <= 0:
                    continue

                close_time = _parse_close_time(m1)
                if close_time is not None:
                    if close_time > cutoff:
                        continue
                    if close_time < _now + LIVE_BUFFER:
                        continue   # game is live or imminent

                ticker = m1.get("ticker", "")
                parent_event_id = normalize_event_key(f"{prefix}{abbr_a} {abbr_b}")

                for side, ask, abbr, is_yes in [
                    (ContractSide.YES, yes_ask, abbr_a, True),
                    (ContractSide.NO,  no_ask,  abbr_b, False),
                ]:
                    key = (ticker, side.value)
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)

                    price = ask
                    contracts.append(MarketContract(
                        platform=self.platform,
                        market_id=ticker,
                        parent_event_id=parent_event_id,
                        parent_event_title=event_title,
                        outcome_label=abbr,   # "OKC", "TOR" — matches DraftKings abbr
                        is_yes_side=is_yes,
                        event_title=f"{event_title} — {abbr} to win",
                        side=side,
                        price=price,
                        payout_per_contract=PAYOUT,
                        decimal_odds=PAYOUT / price,
                        market_type="prediction",
                        close_time=close_time,
                        url=f"https://kalshi.com/markets/{ticker}",
                        raw=m1,
                    ))

            elif len(abbr_pairs) == 3:
                # ── Soccer 3-way game (MLS, EPL, Bundesliga, …) ───────────────
                # Markets are: Home (abbr), TIE, Away (abbr).
                # Identify draw market vs team markets by the "TIE" abbreviation.
                team_pairs = [(m, a) for m, a in abbr_pairs if a != "TIE"]
                tie_pairs  = [(m, a) for m, a in abbr_pairs if a == "TIE"]
                if len(team_pairs) != 2 or len(tie_pairs) != 1:
                    continue   # unexpected structure — skip

                # Use the first team market's close_time for game-level filtering
                m_ref, _ = team_pairs[0]
                close_time = _parse_close_time(m_ref)
                if close_time is not None:
                    if close_time > cutoff:
                        continue
                    if close_time < _now + LIVE_BUFFER:
                        continue   # game is live or imminent

                # parent_event_id uses ONLY the two team abbreviations (not TIE)
                # so it matches the Action Network key: "mls sd stl"
                abbr_team1, abbr_team2 = team_pairs[0][1], team_pairs[1][1]
                parent_event_id = normalize_event_key(
                    f"{prefix}{abbr_team1} {abbr_team2}"
                )

                # Emit YES + NO contracts for all 3 markets
                for market, abbr in abbr_pairs:
                    yes_ask = market.get("yes_ask_dollars")
                    no_ask  = market.get("no_ask_dollars")
                    if yes_ask is None or no_ask is None:
                        continue
                    yes_ask = float(yes_ask)
                    no_ask  = float(no_ask)
                    if yes_ask <= 0 or no_ask <= 0:
                        continue

                    m_ticker = market.get("ticker", "")

                    # YES side: pays $1 if this outcome occurs ("SD wins", "TIE", …)
                    key_yes = (m_ticker, ContractSide.YES.value)
                    if key_yes not in seen_keys:
                        seen_keys.add(key_yes)
                        yes_price = yes_ask
                        contracts.append(MarketContract(
                            platform=self.platform,
                            market_id=m_ticker,
                            parent_event_id=parent_event_id,
                            parent_event_title=event_title,
                            outcome_label=abbr,          # "SD", "TIE", "STL"
                            is_yes_side=True,
                            event_title=f"{event_title} — {abbr} to win",
                            side=ContractSide.YES,
                            price=yes_price,
                            payout_per_contract=PAYOUT,
                            decimal_odds=PAYOUT / yes_price,
                            num_outcomes=3,
                            market_type="prediction",
                            close_time=close_time,
                            url=f"https://kalshi.com/markets/{m_ticker}",
                            raw=market,
                        ))

                    # NO side: pays $1 if this outcome does NOT occur
                    # Label "not_SD" → can only hedge with a sportsbook "SD wins" contract.
                    key_no = (m_ticker, ContractSide.NO.value)
                    if key_no not in seen_keys:
                        seen_keys.add(key_no)
                        no_price = no_ask
                        contracts.append(MarketContract(
                            platform=self.platform,
                            market_id=m_ticker,
                            parent_event_id=parent_event_id,
                            parent_event_title=event_title,
                            outcome_label=f"not_{abbr}",  # "not_SD", "not_TIE", "not_STL"
                            is_yes_side=False,
                            event_title=f"{event_title} — not {abbr}",
                            side=ContractSide.NO,
                            price=no_price,
                            payout_per_contract=PAYOUT,
                            decimal_odds=PAYOUT / no_price,
                            num_outcomes=3,
                            market_type="prediction",
                            close_time=close_time,
                            url=f"https://kalshi.com/markets/{m_ticker}",
                            raw=market,
                        ))

            # else: unsupported number of markets — skip silently

        return contracts

    def _parse_binary_events(
        self,
        events: list[dict],
        cutoff: datetime,
        seen_keys: set[tuple[str, str]],
    ) -> tuple[list[MarketContract], int, int]:
        """
        Parse single-market binary (YES/NO) events for Phase 2.

        Skips:
          • Multi-market events (sports games, categorical elections)
          • Game prop events: "Team A at/vs Team B: Prop Name" (colon after teams)
          • Events closing beyond the cutoff (far future)

        Returns (contracts, skipped_multi, skipped_far_future).
        """
        contracts:         list[MarketContract] = []
        skipped_multi      = 0
        skipped_far_future = 0

        for event in events:
            event_title = event.get("title", "")
            markets     = event.get("markets", [])

            if not event_title or not markets:
                continue

            if len(markets) != 1:
                skipped_multi += 1
                continue

            # Skip game prop events (e.g. "Team A at Team B: Over 215 points")
            if _is_prop_title(event_title):
                skipped_multi += 1
                continue

            m = markets[0]

            yes_ask = m.get("yes_ask_dollars")
            no_ask  = m.get("no_ask_dollars")
            if yes_ask is None or no_ask is None:
                continue
            yes_ask = float(yes_ask)
            no_ask  = float(no_ask)
            if yes_ask <= 0 or no_ask <= 0:
                continue

            close_time = _parse_close_time(m)
            if close_time is not None and close_time > cutoff:
                skipped_far_future += 1
                continue

            yes_price = yes_ask
            no_price  = no_ask
            ticker    = m.get("ticker", "")

            parent_event_id = normalize_event_key(event_title)

            for side, price, outcome_lbl, is_yes in [
                (ContractSide.YES, yes_price, "Yes", True),
                (ContractSide.NO,  no_price,  "No",  False),
            ]:
                key = (ticker, side.value)
                if key in seen_keys:
                    continue
                seen_keys.add(key)

                contracts.append(MarketContract(
                    platform=self.platform,
                    market_id=ticker,
                    parent_event_id=parent_event_id,
                    parent_event_title=event_title,
                    outcome_label=outcome_lbl,
                    is_yes_side=is_yes,
                    event_title=event_title,
                    side=side,
                    price=price,
                    payout_per_contract=PAYOUT,
                    decimal_odds=PAYOUT / price,
                    market_type="prediction",
                    close_time=close_time,
                    url=f"https://kalshi.com/markets/{ticker}",
                    raw=m,
                ))

        return contracts, skipped_multi, skipped_far_future

    async def _fetch_series_games(
        self,
        series_ticker: str,
        cutoff: datetime,
        seen_keys: set[tuple[str, str]],
        sport: str = "",
        now: datetime | None = None,
    ) -> list[MarketContract]:
        """
        Fetch all game events for a specific Kalshi series.
        Paginates until exhausted.

        sport: Action Network slug forwarded to _parse_game_events for
               sport-namespaced parent_event_id construction.
        now:   Current UTC time; forwarded to _parse_game_events for the
               live-game guard (skip games starting within 30 minutes).
        """
        contracts: list[MarketContract] = []
        cursor: str | None = None

        while True:
            params: dict = {
                "limit": 100,
                "with_nested_markets": "true",
                "status": "open",
                "series_ticker": series_ticker,
            }
            if cursor:
                params["cursor"] = cursor

            try:
                resp = await self._throttled_get(f"{BASE_URL}/events", params=params)
                data = resp.json()
            except Exception as exc:
                logger.warning(f"[kalshi] Series {series_ticker} failed: {exc}")
                break

            events = data.get("events", [])
            if not events:
                break

            batch = self._parse_game_events(events, cutoff, seen_keys, sport=sport, now=now)
            contracts.extend(batch)

            cursor = data.get("cursor")
            if not cursor:
                break

        return contracts

    async def fetch_markets(self) -> list[MarketContract]:
        now    = datetime.now(timezone.utc)
        cutoff = now + timedelta(days=NEAR_TERM_DAYS)

        contracts: list[MarketContract] = []
        # Shared dedup key: (market_id, side) — allows YES and NO both through
        seen_keys: set[tuple[str, str]] = set()

        # ── Phase 1: Explicit game series (sports moneylines) ──────────────────
        game_count = 0
        for series in GAME_SERIES:
            sport = SERIES_TO_SPORT.get(series, "")
            series_contracts = await self._fetch_series_games(
                series, cutoff, seen_keys, sport=sport, now=now
            )
            contracts.extend(series_contracts)
            if series_contracts:
                game_count += len(series_contracts)
                logger.info(
                    f"[kalshi] {series}: {len(series_contracts)} game contracts."
                )

        logger.info(f"[kalshi] Phase 1 total: {game_count} game contracts.")

        # ── Phase 2: General pagination (binary political/economic events) ─────
        cursor:    str | None = None
        page_count  = 0
        skipped_multi_total      = 0
        skipped_far_future_total = 0
        binary_count = 0
        consecutive_empty = 0

        while page_count < MAX_EVENT_PAGES:
            params: dict = {
                "limit": 100,
                "with_nested_markets": "true",
                "status": "open",
            }
            if cursor:
                params["cursor"] = cursor

            try:
                resp = await self._throttled_get(f"{BASE_URL}/events", params=params)
                data = resp.json()
            except Exception as exc:
                logger.warning(f"[kalshi] Page {page_count + 1} failed: {exc}")
                break

            events = data.get("events", [])
            batch, s_multi, s_ff = self._parse_binary_events(events, cutoff, seen_keys)
            skipped_multi_total      += s_multi
            skipped_far_future_total += s_ff

            page_new = 0
            for c in batch:
                contracts.append(c)
                binary_count += 1
                page_new += 1

            cursor = data.get("cursor")
            page_count += 1

            if page_new == 0:
                consecutive_empty += 1
            else:
                consecutive_empty = 0

            if consecutive_empty >= CONSECUTIVE_EMPTY_PAGES_LIMIT:
                logger.info(
                    f"[kalshi] Early exit after {page_count} pages "
                    f"({consecutive_empty} consecutive empty)."
                )
                break

            if not cursor or not events:
                break

        logger.info(
            f"[kalshi] Phase 2: {binary_count} binary contracts over {page_count} pages "
            f"(skipped {skipped_multi_total} multi-market, {skipped_far_future_total} far-future)."
        )
        logger.info(
            f"[kalshi] TOTAL: {len(contracts)} contracts "
            f"({game_count} sports game-winner + {binary_count} binary)."
        )
        return contracts
