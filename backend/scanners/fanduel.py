"""
FanDuel sportsbook scanner via Action Network.

Uses Action Network book_id=69 (FanDuel NJ) to fetch FanDuel moneyline odds.
FanDuel typically has the widest game coverage of the three major US books on
Action Network — 40 NBA games vs DraftKings' 24, as of Feb 2025.

All parsing logic lives in ActionNetworkScanner (action_network.py).
parent_event_id format: normalize_event_key("{sport} {home_abbr} {away_abbr}")
→ e.g. "bos den nba" — identical to Kalshi's SERIES_TO_SPORT-prefixed event key.
"""
from __future__ import annotations

from scanners.action_network import ActionNetworkScanner
from models import Platform


class FanDuelScanner(ActionNetworkScanner):
    """FanDuel moneylines via Action Network (book_id=69, FanDuel NJ)."""
    platform = Platform.FANDUEL
    book_id  = 69   # FanDuel NJ — widest game coverage
