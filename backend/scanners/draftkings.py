"""
DraftKings sportsbook scanner via Action Network.

Uses Action Network book_id=68 (DraftKings NJ) to fetch real DraftKings moneyline
odds — not the synthetic Consensus line (id=15) used previously.

All parsing logic lives in ActionNetworkScanner (action_network.py).
parent_event_id format: normalize_event_key("{sport} {home_abbr} {away_abbr}")
→ e.g. "bos den nba" — identical to Kalshi's SERIES_TO_SPORT-prefixed event key.
"""
from __future__ import annotations

from scanners.action_network import ActionNetworkScanner
from models import Platform


class DraftKingsScanner(ActionNetworkScanner):
    """DraftKings moneylines via Action Network (book_id=68, DraftKings NJ)."""
    platform = Platform.DRAFTKINGS
    book_id  = 68   # DK NJ — real DraftKings lines
