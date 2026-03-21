"""
Caesars Sportsbook scanner via Action Network.

Uses Action Network book_id=123 (Caesars NJ) to fetch Caesars moneyline odds.
Caesars often carries slightly different lines from DraftKings and FanDuel,
expanding the set of cross-platform arbitrage opportunities against Kalshi.

All parsing logic lives in ActionNetworkScanner (action_network.py).
parent_event_id format: normalize_event_key("{sport} {home_abbr} {away_abbr}")
→ e.g. "bos den nba" — identical to Kalshi's SERIES_TO_SPORT-prefixed event key.
"""
from __future__ import annotations

from scanners.action_network import ActionNetworkScanner
from models import Platform


class CaesarsScanner(ActionNetworkScanner):
    """Caesars moneylines via Action Network (book_id=123, Caesars NJ)."""
    platform = Platform.CAESARS
    book_id  = 123  # Caesars NJ
