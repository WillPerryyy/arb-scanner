"""
Consensus-line oracle scanner via Action Network — Consensus (book_id=15).

The Consensus line (book_id=15) is the live weighted-average price across all
books tracked by Action Network.  It updates continuously as books move their
lines, so it always reflects the CURRENT efficient-market estimate rather than
a stale opening number.

Why Consensus (book_id=15) over Opening Line (book_id=30)?
  book_id=30 (Opening Line) is set once when a market first opens — typically
  1–7 days before the game.  By game day it can drift 5–15+ percentage points
  from current market consensus (e.g., a team opens at 34% but is now 27% due
  to injury news or sharp action).  Using a stale opening line as the oracle
  produces false-positive value signals: Kalshi at 27% looks like a 7 pp edge
  against a 34% oracle even though the real current probability is also ~27%.

  book_id=15 (Consensus) tracks the market in real time.  If Kalshi is at 27%
  and the consensus across books is also ~27%, the oracle correctly shows no edge.
  If Kalshi is genuinely slow to update (e.g., still at 27% when books have
  moved to 34%), the oracle correctly surfaces the edge.

Available book_ids for scheduled games on Action Network:
  15  Consensus  ~4.9% vig  (live market average — used here)
  30  Open       ~3.8% vig  (opening line — stale by game day)
  68  DraftKings ~4.6% vig
  69  FanDuel    ~4.2% vig
  71  Unknown    ~5.7% vig
  75  Unknown    ~4.7% vig
  3   Pinnacle   — listed in /books but NOT exposed via /scoreboard
  14  Westgate   — only appears for in-progress games, not scheduled

Role in the system:
  Consensus contracts are used ONLY as the oracle probability reference:
    oracle_prob  = 1 / consensus_decimal_odds   (implied probability)
    cross_ev_pct = (oracle_prob × D_kalshi − 1) × 100

  Platform.PINNACLE is the engine's tag for "the oracle platform".
  Oracle contracts are excluded from arbitrage opportunity building in engine.py
  and never surface as bettable legs.

  If you have an Odds API key, configure ODDS_API_KEY in .env and add
  "pinnacle" to BOOKMAKER_PLATFORM_MAP in odds_api.py for actual live
  Pinnacle lines (vig≈2.5%, the global sharp standard) as the oracle instead.
"""
from __future__ import annotations

from scanners.action_network import ActionNetworkScanner
from models import Platform


class PinnacleScanner(ActionNetworkScanner):
    """Consensus oracle via Action Network (book_id=15).

    Registered as Platform.PINNACLE because the engine treats this platform
    as the oracle regardless of the underlying source identity.

    book_id=15: Consensus/live market average — always current, tracks line
    movement in real time.  Used as the reference price for Kalshi value comparisons.
    """
    platform = Platform.PINNACLE
    book_id  = 15   # Consensus line — live market average, always current
