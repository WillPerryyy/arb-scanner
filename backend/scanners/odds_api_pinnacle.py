"""
The Odds API — Pinnacle Oracle (force-scan-only).

Fetches REAL Pinnacle lines (h2h + spreads + totals) directly from The Odds API
and returns them as Platform.PINNACLE MarketContracts.  The engine's existing
oracle infrastructure (pinnacle_price_lookup, kalshi_oracle_map) consumes these
contracts without modification.

Key design constraints:
  • Does NOT extend BaseScanner — never runs in the regular 90-s scheduler.
  • Only called by engine.scan_sharp_value(), which is triggered exclusively by
    POST /api/sharp-value/scan (explicit user action).
  • 500 requests/month budget.  Each sport key costs 1 request regardless of
    whether h2h, spreads, and totals are fetched together.
  • Request quota tracked via backend/data/odds_api_usage.json.
  • Esports are NOT available on The Odds API — sport keys for them silently
    return 0 results; no requests are consumed.

Namespace alignment:
  Reuses TEAM_ABBR_MAP and SPORT_KEY_MAP from scanners/odds_api.py so that
  h2h contracts share the same abbreviation-based parent_event_id namespace as
  Kalshi/DK (e.g. "nba okc tor").  This is essential for the matcher to create
  Kalshi↔Pinnacle hedge pairs that feed the kalshi_oracle_map.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import httpx

from arbitrage.matcher import normalize_event_key
from models import MarketContract, Platform, ContractSide
from scanners.odds_api import TEAM_ABBR_MAP, SPORT_KEY_MAP

logger = logging.getLogger(__name__)

BASE_URL   = "https://api.the-odds-api.com/v4"
USAGE_FILE = Path(__file__).parent.parent / "data" / "odds_api_usage.json"

# ── Sport keys to scan ───────────────────────────────────────────────────────
# One request per key.  Esports omitted — Odds API has no coverage.
# Tennis keys match The Odds API naming convention (aus_open, not australian_open).
PINNACLE_SPORT_KEYS: list[str] = [
    # Basketball
    "basketball_nba",
    "basketball_ncaab",
    "basketball_ncaaw",
    "basketball_wnba",          # added — Kalshi has KXWNBAGAME
    # American football
    "americanfootball_nfl",
    "americanfootball_ncaaf",
    # Baseball
    "baseball_mlb",
    # Hockey
    "icehockey_nhl",            # added — Kalshi has KXNHLGAME
    # Soccer
    "soccer_usa_mls",
    "soccer_epl",
    "soccer_france_ligue_1",
    "soccer_germany_bundesliga",
    "soccer_spain_la_liga",
    "soccer_italy_serie_a",
    "soccer_uefa_champs_league",
    "soccer_uefa_europa_league",
    # Tennis grand slams
    "tennis_atp_wimbledon",
    "tennis_wta_wimbledon",
    "tennis_atp_us_open",
    "tennis_wta_us_open",
    "tennis_atp_french_open",
    "tennis_wta_french_open",
    "tennis_atp_aus_open",
    "tennis_wta_aus_open",
    # MMA / Combat sports
    "mma_mixed_martial_arts",
    "boxing_boxing",
]

# ── Sport key metadata ────────────────────────────────────────────────────────
# Human-readable labels and UI group names for each sport key.
# Consumed by GET /api/sharp-value/sports to drive the frontend checkbox panel.
SPORT_KEY_META: dict[str, dict[str, str]] = {
    "basketball_nba":            {"group": "Basketball",    "label": "NBA"},
    "basketball_ncaab":          {"group": "Basketball",    "label": "NCAA Men's Basketball"},
    "basketball_ncaaw":          {"group": "Basketball",    "label": "NCAA Women's Basketball"},
    "basketball_wnba":           {"group": "Basketball",    "label": "WNBA"},
    "americanfootball_nfl":      {"group": "Football",      "label": "NFL"},
    "americanfootball_ncaaf":    {"group": "Football",      "label": "NCAA Football"},
    "baseball_mlb":              {"group": "Baseball",      "label": "MLB"},
    "icehockey_nhl":             {"group": "Hockey",        "label": "NHL"},
    "soccer_usa_mls":            {"group": "Soccer",        "label": "MLS"},
    "soccer_epl":                {"group": "Soccer",        "label": "EPL"},
    "soccer_france_ligue_1":     {"group": "Soccer",        "label": "Ligue 1"},
    "soccer_germany_bundesliga": {"group": "Soccer",        "label": "Bundesliga"},
    "soccer_spain_la_liga":      {"group": "Soccer",        "label": "La Liga"},
    "soccer_italy_serie_a":      {"group": "Soccer",        "label": "Serie A"},
    "soccer_uefa_champs_league": {"group": "Soccer",        "label": "Champions League"},
    "soccer_uefa_europa_league": {"group": "Soccer",        "label": "Europa League"},
    "tennis_atp_wimbledon":      {"group": "Tennis (ATP)",  "label": "Wimbledon"},
    "tennis_atp_us_open":        {"group": "Tennis (ATP)",  "label": "US Open"},
    "tennis_atp_french_open":    {"group": "Tennis (ATP)",  "label": "French Open"},
    "tennis_atp_aus_open":       {"group": "Tennis (ATP)",  "label": "Australian Open"},
    "tennis_wta_wimbledon":      {"group": "Tennis (WTA)",  "label": "Wimbledon"},
    "tennis_wta_us_open":        {"group": "Tennis (WTA)",  "label": "US Open"},
    "tennis_wta_french_open":    {"group": "Tennis (WTA)",  "label": "French Open"},
    "tennis_wta_aus_open":       {"group": "Tennis (WTA)",  "label": "Australian Open"},
    "mma_mixed_martial_arts":    {"group": "Combat Sports", "label": "MMA / UFC"},
    "boxing_boxing":             {"group": "Combat Sports", "label": "Boxing"},
}


# ── Usage helpers ─────────────────────────────────────────────────────────────

def load_usage() -> dict:
    """Return current quota stats.  Returns safe defaults if file missing."""
    if USAGE_FILE.exists():
        try:
            with open(USAGE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "requests_remaining": 500,
        "requests_used":      0,
        "monthly_limit":      500,
        "last_scan_at":       None,
        "last_scan_cost":     0,
    }


def _save_usage(
    requests_remaining: int,
    requests_used:      int,
    scan_cost:          int,
) -> None:
    """Persist request-quota stats after every scan."""
    data = {
        "requests_remaining": requests_remaining,
        "requests_used":      requests_used,
        "monthly_limit":      500,
        "last_scan_at":       datetime.now(timezone.utc).isoformat(),
        "last_scan_cost":     scan_cost,
    }
    USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(USAGE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    logger.info(
        f"[odds_api_pinnacle] Usage saved — {requests_remaining} remaining "
        f"({requests_used} used, {scan_cost} this scan)"
    )


# ── Oracle class ──────────────────────────────────────────────────────────────

class OddsApiPinnacleOracle:
    """
    Fetches real Pinnacle lines from The Odds API and returns them as
    Platform.PINNACLE MarketContracts ready to be consumed by the engine's
    existing oracle infrastructure.

    Usage:
        async with httpx.AsyncClient(timeout=20.0) as client:
            oracle = OddsApiPinnacleOracle(client)
            contracts, remaining = await oracle.fetch_markets(api_key)
    """

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def fetch_markets(
        self,
        api_key:    str,
        sport_keys: list[str] | None = None,
    ) -> tuple[list[MarketContract], int]:
        """
        Fetch Pinnacle h2h + spreads + totals from the Odds API.

        Each sport key counts as exactly ONE API request (all three market
        types are bundled in a single call).

        Args:
            api_key:    The Odds API key (500 req/month on the free tier).
            sport_keys: Which sports to fetch.  Defaults to PINNACLE_SPORT_KEYS.

        Returns:
            A tuple of:
            - List of MarketContracts tagged Platform.PINNACLE.
            - The ``x-requests-remaining`` header value from the most recent
              successful response (best estimate of remaining monthly quota).
        """
        if sport_keys is None:
            sport_keys = PINNACLE_SPORT_KEYS

        contracts:          list[MarketContract] = []
        requests_remaining: int = 500   # optimistic default until first header
        requests_used:      int = 0
        scan_cost:          int = 0

        for sport in sport_keys:
            try:
                resp = await self._client.get(
                    f"{BASE_URL}/sports/{sport}/odds",
                    params={
                        "apiKey":      api_key,
                        "regions":     "us",
                        "markets":     "h2h,spreads,totals",
                        "oddsFormat":  "decimal",   # Pinnacle lines are already decimal
                        "bookmakers":  "pinnacle",
                    },
                    timeout=15.0,
                )

                # Capture quota headers from each successful response
                if "x-requests-remaining" in resp.headers:
                    requests_remaining = int(resp.headers["x-requests-remaining"])
                if "x-requests-used" in resp.headers:
                    requests_used = int(resp.headers["x-requests-used"])
                scan_cost += 1

                # 422 = sport key has no current events (e.g. off-season tennis slam)
                if resp.status_code == 422:
                    logger.debug(
                        f"[odds_api_pinnacle] {sport}: 422 — no events or "
                        f"unavailable key (esports, off-season). Skipped."
                    )
                    scan_cost -= 1   # Uncounted — 422 costs 0 requests
                    continue

                resp.raise_for_status()
                events = resp.json()

                if not isinstance(events, list):
                    logger.debug(
                        f"[odds_api_pinnacle] {sport}: unexpected response "
                        f"type {type(events)} — skipped."
                    )
                    continue

            except httpx.HTTPStatusError as exc:
                logger.warning(
                    f"[odds_api_pinnacle] {sport} HTTP {exc.response.status_code}: {exc}"
                )
                continue
            except Exception as exc:
                logger.warning(f"[odds_api_pinnacle] {sport} failed: {exc}")
                continue

            sport_abbr = SPORT_KEY_MAP.get(sport, "")
            sport_contracts = self._parse_events(events, sport, sport_abbr)
            contracts.extend(sport_contracts)
            logger.debug(
                f"[odds_api_pinnacle] {sport}: {len(sport_contracts)} contracts"
            )

        _save_usage(requests_remaining, requests_used, scan_cost)
        logger.info(
            f"[odds_api_pinnacle] {len(contracts)} Pinnacle contracts from "
            f"{len(sport_keys)} sport keys.  "
            f"{requests_remaining} requests remaining this month."
        )
        return contracts, requests_remaining

    # ── Internal parsing ──────────────────────────────────────────────────────

    def _parse_events(
        self,
        events:     list[dict],
        sport:      str,
        sport_abbr: str,
    ) -> list[MarketContract]:
        contracts: list[MarketContract] = []

        for event in events:
            home       = event.get("home_team", "")
            away       = event.get("away_team", "")
            game_title = f"{home} vs {away}"
            event_id   = event.get("id", "")

            # Team abbreviations — critical for landing in the SAME bucket as
            # Kalshi/DK contracts (e.g. "nba okc tor" matches DK's exact key).
            home_abbr = TEAM_ABBR_MAP.get(home.lower(), "")
            away_abbr = TEAM_ABBR_MAP.get(away.lower(), "")

            # Abbreviation-based parent_event_id (h2h, matches Kalshi/DK)
            if sport_abbr and home_abbr and away_abbr:
                abbr_parent_id = normalize_event_key(
                    f"{sport_abbr} {home_abbr} {away_abbr}"
                )
            else:
                abbr_parent_id = normalize_event_key(game_title)

            # Only the Pinnacle bookmaker is requested
            for bookmaker in event.get("bookmakers", []):
                if bookmaker.get("key") != "pinnacle":
                    continue

                for market in bookmaker.get("markets", []):
                    market_key = market.get("key")
                    if market_key not in ("h2h", "spreads", "totals"):
                        continue

                    outcomes = market.get("outcomes", [])
                    # Need at least 2 outcomes for any market type
                    if len(outcomes) < 2:
                        continue

                    for i, outcome in enumerate(outcomes):
                        team_name = outcome.get("name", "")
                        dec_odds  = outcome.get("price")
                        if dec_odds is None or float(dec_odds) <= 1.0:
                            continue

                        dec_odds = float(dec_odds)
                        point    = outcome.get("point")  # spreads and totals only

                        # ── Per-market label + parent_event_id logic ──────────
                        if market_key == "h2h":
                            is_home = (team_name == home)
                            # Use abbreviation label when available for namespace
                            # alignment with DK/Kalshi (e.g. "OKC" not "Oklahoma
                            # City Thunder").  Upper-case to match DK convention.
                            if sport_abbr and home_abbr and away_abbr:
                                outcome_label    = (home_abbr if is_home else away_abbr).upper()
                            else:
                                outcome_label    = team_name
                            market_parent_id = abbr_parent_id

                        elif market_key == "spreads":
                            if point is None:
                                continue
                            abs_point  = abs(point)
                            abs_str    = (
                                f"{abs_point:.1f}" if abs_point != int(abs_point)
                                else f"{int(abs_point)}"
                            )
                            signed_str = (
                                f"{point:+.1f}" if point != int(point)
                                else f"{int(point):+d}"
                            )
                            is_home          = (team_name == home)
                            outcome_label    = f"{team_name} {signed_str}"
                            market_parent_id = normalize_event_key(
                                f"{game_title} spread {abs_str}"
                            )

                        else:  # totals
                            if point is None:
                                continue
                            point_str = (
                                f"{point:.1f}" if point != int(point)
                                else f"{int(point)}"
                            )
                            is_home          = (team_name.lower() == "over")
                            outcome_label    = f"{team_name} {point_str}"
                            market_parent_id = normalize_event_key(
                                f"{game_title} total {point_str}"
                            )
                        # ─────────────────────────────────────────────────────

                        side = ContractSide.YES if is_home else ContractSide.NO

                        _mtype = (
                            "moneyline" if market_key == "h2h"
                            else "spread" if market_key == "spreads"
                            else "total"
                        )
                        contracts.append(MarketContract(
                            platform=Platform.PINNACLE,
                            market_id=f"{event_id}_pinnacle_{market_key}_{i}",
                            parent_event_id=market_parent_id,
                            parent_event_title=game_title,
                            outcome_label=outcome_label,
                            is_yes_side=is_home,
                            event_title=f"{game_title} [Pinnacle] ({market_key})",
                            side=side,
                            price=1.0 / dec_odds,
                            payout_per_contract=1.0,
                            decimal_odds=dec_odds,
                            market_type=_mtype,
                            raw={
                                "team":        team_name,
                                "decimal_odds": dec_odds,
                                "sport":        sport,
                                "market":       market_key,
                                "point":        point,
                                "event_id":     event_id,
                            },
                        ))

        return contracts
