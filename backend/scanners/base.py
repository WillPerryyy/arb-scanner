from __future__ import annotations
import asyncio
import logging
from abc import ABC, abstractmethod

import httpx

from models import MarketContract, ScannerStatus, Platform

logger = logging.getLogger(__name__)


class BaseScanner(ABC):
    platform: Platform
    _min_request_interval: float = 1.0

    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client
        self._last_request_at: float = 0.0

    async def _throttled_get(self, url: str, **kwargs) -> httpx.Response:
        """Rate-limited GET with exponential backoff on 429/5xx.

        Client errors (4xx, except 429 rate-limits) are NOT retried — they
        indicate a fundamental problem with the request (bad key, bad endpoint)
        that won't be resolved by retrying.  Network errors and 5xx server errors
        are retried up to 4 times with exponential backoff.
        """
        loop = asyncio.get_event_loop()
        elapsed = loop.time() - self._last_request_at
        if elapsed < self._min_request_interval:
            await asyncio.sleep(self._min_request_interval - elapsed)

        for attempt in range(4):
            try:
                resp = await self.client.get(url, **kwargs)
                self._last_request_at = asyncio.get_event_loop().time()
                if resp.status_code == 429:
                    await asyncio.sleep(2 ** attempt)
                    continue
                resp.raise_for_status()
                return resp
            except httpx.HTTPStatusError as exc:
                # 4xx client errors (except 429) won't resolve with retrying — raise immediately.
                if 400 <= exc.response.status_code < 500:
                    raise
                if attempt == 3:
                    raise
                logger.warning(f"[{self.platform}] attempt {attempt+1} failed: {exc}")
                await asyncio.sleep(2 ** attempt)
            except httpx.RequestError as exc:
                if attempt == 3:
                    raise
                logger.warning(f"[{self.platform}] attempt {attempt+1} failed: {exc}")
                await asyncio.sleep(2 ** attempt)

        raise RuntimeError(f"All retries exhausted for {url}")

    @abstractmethod
    async def fetch_markets(self) -> list[MarketContract]:
        ...

    async def scan(self) -> tuple[list[MarketContract], ScannerStatus]:
        from datetime import datetime, timezone
        status = ScannerStatus(platform=self.platform)
        try:
            contracts = await self.fetch_markets()
            status.markets_found = len(contracts)
            status.is_healthy = True
            status.last_scanned_at = datetime.now(timezone.utc)
        except Exception as exc:
            logger.error(f"[{self.platform}] scan failed: {exc}")
            contracts = []
            status.error = str(exc)
            status.is_healthy = False
            status.last_scanned_at = datetime.now(timezone.utc)
        return contracts, status
