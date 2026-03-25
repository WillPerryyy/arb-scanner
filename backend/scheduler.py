"""
Background polling scheduler.
Runs run_full_scan() and fetch_crypto_markets() every SCAN_INTERVAL_SECONDS,
stores results in cache, and broadcasts updates to all connected WebSocket clients.
"""
from __future__ import annotations
import asyncio
import json
import logging
from typing import TYPE_CHECKING

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from arbitrage.engine import run_full_scan
from scanners.kalshi_crypto import fetch_crypto_markets
from cache import cache
from config import settings

if TYPE_CHECKING:
    from fastapi import WebSocket

logger = logging.getLogger(__name__)

# Populated by main.py — holds active WebSocket connections
active_connections: list["WebSocket"] = []

scheduler = AsyncIOScheduler()
_scan_lock = asyncio.Lock()


async def scan_and_broadcast() -> None:
    if _scan_lock.locked():
        logger.warning("Scan already in progress — skipping this cycle.")
        return
    async with _scan_lock:
        logger.info("Starting arbitrage scan cycle...")
        try:
            # Run regular arb scan and crypto scan concurrently.
            # The crypto scanner uses its own HTTP client so it doesn't block the
            # main scan; run_full_scan() creates its own client internally.
            async with httpx.AsyncClient(timeout=25.0, follow_redirects=True) as crypto_client:
                (
                    (opportunities, ev_edges, value_ops, statuses, kalshi_sport_counts, near_certainty),
                    (crypto_result, _crypto_status),
                ) = await asyncio.gather(
                    run_full_scan(),
                    fetch_crypto_markets(crypto_client),
                )

            cache.set("latest_opportunities",    opportunities)
            cache.set("latest_ev_edges",         ev_edges)
            cache.set("latest_value_ops",        value_ops)
            cache.set("scanner_status",          statuses)
            cache.set("kalshi_sport_counts",     kalshi_sport_counts)
            cache.set("crypto_scan_result",      crypto_result)
            cache.set("near_certainty_markets",  near_certainty)

            payload = json.dumps({
                "type": "opportunities_update",
                "payload": {
                    "opportunities":        [o.model_dump(mode="json") for o in opportunities],
                    "ev_edges":             [e.model_dump(mode="json") for e in ev_edges],
                    "value_ops":            [v.model_dump(mode="json") for v in value_ops],
                    "scanner_status":       [s.model_dump(mode="json") for s in statuses],
                    "count":                len(opportunities),
                    "ev_edges_count":       len(ev_edges),
                    "value_ops_count":      len(value_ops),
                    # Crypto markets are included in every broadcast for real-time updates
                    "crypto_markets":       [m.model_dump(mode="json") for m in crypto_result.markets],
                    "crypto_arb_count":     crypto_result.arb_count,
                    # Near-certainty markets (≥97¢) across all platforms
                    "near_certainty":       [m.model_dump(mode="json") for m in near_certainty],
                    "near_certainty_count": len(near_certainty),
                },
            })

            dead: list = []
            for ws in list(active_connections):
                try:
                    await ws.send_text(payload)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                try:
                    active_connections.remove(ws)
                except ValueError:
                    pass

            logger.info(
                f"Broadcasted {len(opportunities)} opportunities and "
                f"{len(crypto_result.markets)} crypto markets to "
                f"{len(active_connections)} WebSocket clients."
            )
        except Exception as exc:
            logger.error(f"Scan cycle failed: {exc}", exc_info=True)


def start_scheduler() -> None:
    scheduler.add_job(
        scan_and_broadcast,
        "interval",
        seconds=settings.SCAN_INTERVAL_SECONDS,
        id="arb_scan",
        replace_existing=True,
    )
    scheduler.start()
    # Trigger an immediate first scan
    asyncio.get_event_loop().create_task(scan_and_broadcast())
    logger.info(
        f"Scheduler started. Scanning every {settings.SCAN_INTERVAL_SECONDS}s."
    )
