"""
PredictIt public API — no auth required.
https://www.predictit.org/api/marketdata/all/
Returns all open markets in a single response.
Winning contracts pay $1.00 per share.
Fees: 10% on profits + 5% on withdrawal.

Data structure:
  - Each "market" is a parent event (e.g. "Who will win the 2024 election?")
  - Each "contract" within a market is one outcome (e.g. "Republican", "Democrat")
  - All contracts within a market are mutually exclusive — exactly one pays $1.00

We ONLY emit contracts from single-contract markets (binary YES/NO questions).
Multi-contract markets (multiple candidates) are skipped — you cannot guarantee
a payout by buying only two of N candidates.

For single-contract markets we emit both the YES and NO leg so the matcher can
find cross-platform arbs (e.g. PredictIt YES + Kalshi NO on the same question).
"""
from __future__ import annotations
import logging

from scanners.base import BaseScanner
from arbitrage.matcher import normalize_event_key
from models import MarketContract, Platform, ContractSide

logger = logging.getLogger(__name__)

API_URL = "https://www.predictit.org/api/marketdata/all/"
PAYOUT  = 1.0


class PredictItScanner(BaseScanner):
    platform = Platform.PREDICTIT
    _min_request_interval = 3.0

    async def fetch_markets(self) -> list[MarketContract]:
        contracts: list[MarketContract] = []
        skipped_multi = 0

        resp = await self._throttled_get(API_URL)
        data = resp.json()

        for market in data.get("markets", []):
            market_name = market.get("name", "")
            market_id   = str(market.get("id", ""))
            market_url  = f"https://www.predictit.org/markets/detail/{market_id}"
            market_contracts = market.get("contracts", [])

            # ONLY process single-contract (binary YES/NO) markets.
            # Multi-contract markets (multiple candidates) are skipped.
            if len(market_contracts) != 1:
                skipped_multi += 1
                continue

            contract = market_contracts[0]
            contract_name = contract.get("name", "")
            contract_id   = str(contract.get("id", ""))

            yes_price = contract.get("bestBuyYesCost")
            no_price  = contract.get("bestBuyNoCost")

            if yes_price is None and no_price is None:
                continue

            # Canonical event key for cross-platform matching
            parent_event_id = normalize_event_key(market_name)

            # YES leg
            if yes_price is not None:
                yes_price = float(yes_price)
                if yes_price > 0:
                    contracts.append(MarketContract(
                        platform=self.platform,
                        market_id=contract_id,
                        parent_event_id=parent_event_id,
                        parent_event_title=market_name,
                        outcome_label="Yes",
                        is_yes_side=True,
                        event_title=market_name,
                        side=ContractSide.YES,
                        price=yes_price,
                        payout_per_contract=PAYOUT,
                        decimal_odds=PAYOUT / yes_price,
                        url=market_url,
                        raw=contract,
                    ))

            # NO leg
            if no_price is not None:
                no_price = float(no_price)
                if no_price > 0:
                    contracts.append(MarketContract(
                        platform=self.platform,
                        market_id=f"{contract_id}_no",
                        parent_event_id=parent_event_id,
                        parent_event_title=market_name,
                        outcome_label="No",
                        is_yes_side=False,
                        event_title=market_name,
                        side=ContractSide.NO,
                        price=no_price,
                        payout_per_contract=PAYOUT,
                        decimal_odds=PAYOUT / no_price,
                        url=market_url,
                        raw=contract,
                    ))

        logger.info(
            f"[predictit] Fetched {len(contracts)} contracts "
            f"(skipped {skipped_multi} multi-contract markets)."
        )
        return contracts
