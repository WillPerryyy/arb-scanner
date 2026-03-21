"""
Crypto prediction markets scanner — Kalshi + Polymarket.

Kalshi series fetched:
  KXBTC15M — BTC 15-minute "price up or down?" binary
  KXETH15M — ETH 15-minute "price up or down?" binary
  KXBCH15M — BCH 15-minute "price up or down?" binary
  KXBTCD   — BTC daily "above $X?" markets (multiple price thresholds per event)
  KXETHD   — ETH daily "above $X?" markets

Polymarket crypto markets are fetched via the Gamma API (public, no key):
  GET https://gamma-api.polymarket.com/markets?tag_slug=crypto&active=true
  Filtered to BTC / ETH / BCH questions with a parseable dollar threshold.

Arb detection:
  For each market, if yes_ask + no_ask < 1.00 a guaranteed profit exists by
  buying both sides.  The payout is $1.00 regardless of resolution.

Note: DraftKings Predictions (predictions.draftkings.com) is a Kalshi broker
and shares the same underlying order book, so intra-Kalshi arbs apply there too.
"""
from __future__ import annotations
import json
import logging
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import httpx

from models import CryptoMarket, CryptoScanResult, ScannerStatus, Platform

logger = logging.getLogger(__name__)

BASE_URL       = "https://api.elections.kalshi.com/trade-api/v2"
GAMMA_BASE_URL = "https://gamma-api.polymarket.com"

# (series_ticker, asset_symbol, market_type_label)
CRYPTO_SERIES: list[tuple[str, str, str]] = [
    ("KXBTC15M", "BTC", "15m"),
    ("KXETH15M", "ETH", "15m"),
    ("KXBCH15M", "BCH", "15m"),
    ("KXBTCD",   "BTC", "daily"),
    ("KXETHD",   "ETH", "daily"),
]

# Keywords that identify crypto assets in Polymarket questions
_ASSET_KEYWORDS: list[tuple[str, str]] = [
    ("BTC",     "BTC"),
    ("Bitcoin", "BTC"),
    ("ETH",     "ETH"),
    ("Ether",   "ETH"),
    ("BCH",     "BCH"),
    ("Bitcoin Cash", "BCH"),
]

# Skip markets closing within this many minutes (not enough time to place both legs)
MIN_MINUTES_TO_CLOSE = 5


def _find_cross_platform_arbs(
    kalshi_markets: list[CryptoMarket],
    poly_markets: list[CryptoMarket],
) -> list[CryptoMarket]:
    """
    Find cross-platform arbitrage between Kalshi and Polymarket crypto markets.

    Valid pairs require the SAME asset AND the SAME floor_strike AND resolution
    times within 24 hours of each other.  Two arb combinations are checked:

      • Kalshi YES + Polymarket NO:  kalshi_yes_ask + poly_no_ask < 1.00
      • Kalshi NO  + Polymarket YES: kalshi_no_ask  + poly_yes_ask < 1.00

    NOTE: Polymarket prices come from outcomePrices (last-traded / mid-market).
    Actual ask prices on Polymarket will be slightly higher than these values,
    so detected arbs should be verified on-platform before executing.
    """
    cross_arbs: list[CryptoMarket] = []

    # Index Polymarket markets by (asset, floor_strike)
    poly_by_key: dict[tuple[str, float], list[CryptoMarket]] = defaultdict(list)
    for pm in poly_markets:
        poly_by_key[(pm.asset, pm.floor_strike)].append(pm)

    for kal in kalshi_markets:
        key = (kal.asset, kal.floor_strike)
        candidates = poly_by_key.get(key, [])

        for poly in candidates:
            # Require resolution windows within 24 hours of each other
            time_diff = abs((kal.close_time - poly.close_time).total_seconds())
            if time_diff > 86_400:
                continue

            earlier_close = min(kal.close_time, poly.close_time)

            # Combination A: Buy Kalshi YES + Buy Polymarket NO
            cost_a = kal.yes_ask + poly.no_ask
            if cost_a < 1.0:
                cross_arbs.append(CryptoMarket(
                    platform       = "cross",
                    event_ticker   = f"{kal.event_ticker}+{poly.event_ticker}",
                    market_ticker  = f"{kal.market_ticker}|{poly.market_ticker}:a",
                    asset          = kal.asset,
                    market_type    = "cross",
                    title          = (
                        f"[Kalshi YES + Poly NO] {kal.asset} >{kal.floor_strike:,.0f} "
                        f"— verify Poly ask prices before trading"
                    ),
                    close_time     = earlier_close,
                    floor_strike   = kal.floor_strike,
                    yes_ask        = kal.yes_ask,
                    no_ask         = poly.no_ask,
                    total_cost     = cost_a,
                    is_arb         = True,
                    net_profit_pct = (1.0 - cost_a) / cost_a * 100.0,
                    url            = kal.url,
                ))

            # Combination B: Buy Kalshi NO + Buy Polymarket YES
            cost_b = kal.no_ask + poly.yes_ask
            if cost_b < 1.0:
                cross_arbs.append(CryptoMarket(
                    platform       = "cross",
                    event_ticker   = f"{kal.event_ticker}+{poly.event_ticker}",
                    market_ticker  = f"{kal.market_ticker}|{poly.market_ticker}:b",
                    asset          = kal.asset,
                    market_type    = "cross",
                    title          = (
                        f"[Kalshi NO + Poly YES] {kal.asset} >{kal.floor_strike:,.0f} "
                        f"— verify Poly ask prices before trading"
                    ),
                    close_time     = earlier_close,
                    floor_strike   = kal.floor_strike,
                    yes_ask        = poly.yes_ask,
                    no_ask         = kal.no_ask,
                    total_cost     = cost_b,
                    is_arb         = True,
                    net_profit_pct = (1.0 - cost_b) / cost_b * 100.0,
                    url            = kal.url,
                ))

    return cross_arbs


async def fetch_crypto_markets(
    client: httpx.AsyncClient,
) -> tuple[CryptoScanResult, ScannerStatus]:
    """
    Fetch all open crypto events from Kalshi and detect intra-market arbs.

    Returns a CryptoScanResult (all markets, arb count, scan timestamp) and
    a ScannerStatus for dashboard display.
    """
    now     = datetime.now(timezone.utc)
    min_close = now + timedelta(minutes=MIN_MINUTES_TO_CLOSE)
    markets: list[CryptoMarket] = []
    errors:  list[str]          = []

    for i, (series_ticker, asset, market_type) in enumerate(CRYPTO_SERIES):
        if i > 0:
            # Small pause to stay within Kalshi's rate limit when running alongside
            # the main KalshiScanner (which also makes requests every 90 seconds).
            import asyncio as _asyncio
            await _asyncio.sleep(0.8)
        try:
            resp = await client.get(
                f"{BASE_URL}/events",
                params={
                    "series_ticker":       series_ticker,
                    "status":              "open",
                    "limit":               100,
                    "with_nested_markets": "true",
                },
                timeout=15.0,
            )
            # Retry once on 429
            if resp.status_code == 429:
                import asyncio as _asyncio
                await _asyncio.sleep(2.0)
                resp = await client.get(
                    f"{BASE_URL}/events",
                    params={
                        "series_ticker":       series_ticker,
                        "status":              "open",
                        "limit":               100,
                        "with_nested_markets": "true",
                    },
                    timeout=15.0,
                )
            resp.raise_for_status()
            events = resp.json().get("events", [])

            for event in events:
                event_ticker  = event.get("event_ticker", "")
                event_markets = event.get("markets", [])

                for market in event_markets:
                    # Parse close_time
                    close_str = market.get("close_time") or ""
                    if not close_str:
                        continue
                    try:
                        close_time = datetime.fromisoformat(
                            close_str.replace("Z", "+00:00")
                        )
                    except ValueError:
                        continue

                    if close_time < min_close:
                        continue  # Already closed or not enough time to trade

                    # Parse prices (new API field names: yes_ask_dollars / no_ask_dollars)
                    yes_ask_raw = market.get("yes_ask_dollars")
                    no_ask_raw  = market.get("no_ask_dollars")
                    if yes_ask_raw is None or no_ask_raw is None:
                        continue
                    try:
                        yes_ask = float(yes_ask_raw)
                        no_ask  = float(no_ask_raw)
                    except (ValueError, TypeError):
                        continue

                    if yes_ask <= 0 or no_ask <= 0:
                        continue

                    floor_strike = float(market.get("floor_strike") or 0.0)
                    total_cost   = yes_ask + no_ask
                    is_arb       = total_cost < 1.0
                    net_profit_pct = (
                        (1.0 - total_cost) / total_cost * 100.0
                        if is_arb else 0.0
                    )

                    market_ticker = market.get("ticker", "")
                    title = (
                        market.get("title")
                        or market.get("yes_sub_title")
                        or event.get("title")
                        or event_ticker
                    )

                    markets.append(CryptoMarket(
                        platform       = "kalshi",
                        event_ticker   = event_ticker,
                        market_ticker  = market_ticker,
                        asset          = asset,
                        market_type    = market_type,
                        title          = title,
                        close_time     = close_time,
                        floor_strike   = floor_strike,
                        yes_ask        = yes_ask,
                        no_ask         = no_ask,
                        total_cost     = total_cost,
                        is_arb         = is_arb,
                        net_profit_pct = net_profit_pct,
                        url            = f"https://kalshi.com/markets/{event_ticker}",
                    ))

        except Exception as exc:
            msg = f"{series_ticker}: {exc}"
            logger.warning(f"[kalshi_crypto] {msg}")
            errors.append(msg)

    # ── Polymarket crypto markets ─────────────────────────────────────────────
    poly_markets = await _fetch_polymarket_crypto(client, now, min_close)

    # ── Cross-platform arb detection (Kalshi ↔ Polymarket) ───────────────────
    cross_arbs = _find_cross_platform_arbs(markets, poly_markets)
    if cross_arbs:
        logger.info(
            f"[kalshi_crypto] {len(cross_arbs)} cross-platform arb(s) found "
            f"(Kalshi ↔ Polymarket, same asset+strike+window). "
            f"Polymarket prices are mid-market — verify asks on-platform."
        )

    markets.extend(poly_markets)
    markets.extend(cross_arbs)

    arb_count = sum(1 for m in markets if m.is_arb)
    kalshi_count = sum(1 for m in markets if m.platform == "kalshi")
    logger.info(
        f"[kalshi_crypto] {kalshi_count} Kalshi + {len(poly_markets)} Polymarket = "
        f"{len(markets)} total — {arb_count} arbs found."
    )

    result = CryptoScanResult(
        markets    = markets,
        arb_count  = arb_count,
        scanned_at = now,
    )
    status = ScannerStatus(
        platform       = Platform.KALSHI,
        last_scanned_at = now,
        markets_found  = len(markets),
        is_healthy     = len(errors) < len(CRYPTO_SERIES),
        error          = "; ".join(errors) if errors else None,
    )
    return result, status


async def _fetch_polymarket_crypto(
    client: httpx.AsyncClient,
    now: datetime,
    min_close: datetime,
) -> list[CryptoMarket]:
    """
    Fetch Polymarket crypto prediction markets via the public Gamma API.

    Filters for active binary markets whose question mentions a crypto asset
    (BTC/ETH/BCH) and contains a parseable dollar threshold (floor_strike).
    """
    markets: list[CryptoMarket] = []

    try:
        resp = await client.get(
            f"{GAMMA_BASE_URL}/markets",
            params={
                "limit":     100,
                "offset":    0,
                "active":    "true",
                "closed":    "false",
                "tag_slug":  "crypto",
                "order":     "volume24hr",
                "ascending": "false",
            },
            timeout=15.0,
        )
        resp.raise_for_status()
        raw_markets = resp.json()
    except Exception as exc:
        logger.warning(f"[kalshi_crypto/polymarket] fetch failed: {exc}")
        return markets

    for m in raw_markets:
        question = m.get("question", "") or ""

        # Identify asset
        asset: str | None = None
        for keyword, symbol in _ASSET_KEYWORDS:
            if keyword.lower() in question.lower():
                asset = symbol
                break
        if not asset:
            continue

        # Parse floor_strike from question text — e.g. "above $82,000" → 82000.0
        dollar_matches = re.findall(r"\$([\d,]+(?:\.\d+)?)", question)
        if not dollar_matches:
            continue
        try:
            floor_strike = float(dollar_matches[0].replace(",", ""))
        except ValueError:
            continue

        # Parse close_time from endDate
        end_date_str = m.get("endDate") or m.get("end_date_iso") or ""
        if not end_date_str:
            continue
        try:
            close_time = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
        except ValueError:
            continue

        if close_time < min_close:
            continue

        # Determine market_type from resolution window
        days_to_close = (close_time - now).days
        market_type = "daily" if days_to_close <= 1 else "weekly"

        # Parse prices (may be JSON-encoded strings)
        outcome_prices = m.get("outcomePrices") or []
        if isinstance(outcome_prices, str):
            try:
                outcome_prices = json.loads(outcome_prices)
            except Exception:
                continue
        if len(outcome_prices) < 2:
            continue
        try:
            yes_ask = float(outcome_prices[0])
            no_ask  = float(outcome_prices[1])
        except (ValueError, TypeError):
            continue

        if yes_ask <= 0 or no_ask <= 0:
            continue

        total_cost     = yes_ask + no_ask
        is_arb         = total_cost < 1.0
        net_profit_pct = (1.0 - total_cost) / total_cost * 100.0 if is_arb else 0.0

        market_id  = str(m.get("id", ""))
        slug       = m.get("slug", market_id)
        url        = f"https://polymarket.com/event/{slug}"

        markets.append(CryptoMarket(
            platform       = "polymarket",
            event_ticker   = market_id,
            market_ticker  = market_id,
            asset          = asset,
            market_type    = market_type,
            title          = question,
            close_time     = close_time,
            floor_strike   = floor_strike,
            yes_ask        = yes_ask,
            no_ask         = no_ask,
            total_cost     = total_cost,
            is_arb         = is_arb,
            net_profit_pct = net_profit_pct,
            url            = url,
        ))

    return markets
