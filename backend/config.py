from __future__ import annotations
from pydantic_settings import BaseSettings
from models import Platform, PlatformFees


class Settings(BaseSettings):
    SCAN_INTERVAL_SECONDS:      int   = 90
    CACHE_TTL_SECONDS:          int   = 300   # 5 min — scan takes ~90s, TTL must exceed that
    MIN_NET_PROFIT_DOLLARS:     float = 0.001
    FUZZY_MATCH_THRESHOLD:      float = 0.60

    # Optional API keys — scanners skip gracefully when empty
    ODDS_API_KEY:               str = ""
    KALSHI_API_KEY:             str = ""
    POLYMARKET_API_KEY:         str = ""

    class Config:
        env_file = ".env"


PLATFORM_FEES: dict[Platform, PlatformFees] = {
    Platform.KALSHI:     PlatformFees(profit_fee_pct=0.07),
    Platform.POLYMARKET: PlatformFees(trade_fee_pct=0.02),
    Platform.PREDICTIT:  PlatformFees(profit_fee_pct=0.10, withdrawal_fee_pct=0.05),
    Platform.ODDS_API:   PlatformFees(),
    Platform.DRAFTKINGS: PlatformFees(),
    Platform.FANDUEL:    PlatformFees(),
    Platform.BETMGM:     PlatformFees(),
    Platform.CAESARS:    PlatformFees(),
}

settings = Settings()
