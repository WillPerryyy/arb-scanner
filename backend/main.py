from __future__ import annotations
import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from cache import cache
from config import settings
from models import (
    OpportunitiesResponse, EvEdgesResponse, ValueResponse,
    ArbitrageOpportunity, EvEdgeOpportunity, ValueOpportunity, ScannerStatus,
    CryptoScanResult, NearCertaintyMarket,
)
from scheduler import start_scheduler, active_connections, scan_and_broadcast

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    start_scheduler()
    yield
    from scheduler import scheduler
    scheduler.shutdown(wait=False)


app = FastAPI(
    title="Arb Scanner API",
    version="0.1.0",
    description="Real-time arbitrage scanner for prediction markets and sportsbooks.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:5174",
        "http://localhost:5175",
        "http://127.0.0.1:5173",
        # Vercel deployments
        "https://frontend-sage-eight-55.vercel.app",
        "https://*.vercel.app",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── REST Endpoints ─────────────────────────────────────────────────────────────

@app.get("/api/opportunities", response_model=OpportunitiesResponse)
async def get_opportunities(
    min_profit: float = Query(0.001, ge=0, description="Minimum net profit in dollars"),
    platforms:  list[str] = Query(default=[], description="Filter by platform names"),
    arb_type:   list[str] = Query(default=[], description="Filter by arb type"),
    limit:      int = Query(100, ge=1, le=1000),
):
    """Return cached arbitrage opportunities with optional filters."""
    opps: list[ArbitrageOpportunity] = (
        cache.get("latest_opportunities", settings.CACHE_TTL_SECONDS) or []
    )
    statuses: list[ScannerStatus] = (
        cache.get("scanner_status", settings.CACHE_TTL_SECONDS) or []
    )

    if min_profit > 0:
        opps = [o for o in opps if o.net_profit >= min_profit]

    if platforms:
        opps = [
            o for o in opps
            if o.leg_yes.contract.platform.value in platforms
            or o.leg_no.contract.platform.value in platforms
        ]

    if arb_type:
        opps = [o for o in opps if o.arb_type in arb_type]

    return OpportunitiesResponse(
        opportunities=opps[:limit],
        scanner_status=statuses,
        scanned_at=datetime.now(timezone.utc),
        total_markets=sum(s.markets_found for s in statuses),
    )


@app.get("/api/health")
async def health():
    statuses: list[ScannerStatus] = (
        cache.get("scanner_status", 300) or []
    )
    opps: list[ArbitrageOpportunity] = (
        cache.get("latest_opportunities", 300) or []
    )
    return {
        "status": "ok",
        "active_scanners": len(statuses),
        "healthy_scanners": sum(1 for s in statuses if s.is_healthy),
        "total_markets": sum(s.markets_found for s in statuses),
        "opportunities_cached": len(opps),
        "ws_clients": len(active_connections),
    }


@app.get("/api/ev-edges", response_model=EvEdgesResponse)
async def get_ev_edges(
    min_avg_return_pct: float = Query(1.0, ge=0, description="Min average return %"),
    limit:              int   = Query(100, ge=1, le=1000),
):
    """Return cached EV-edge opportunities (positive average return, non-guaranteed arbs)."""
    ev_edges: list[EvEdgeOpportunity] = (
        cache.get("latest_ev_edges", settings.CACHE_TTL_SECONDS) or []
    )
    statuses: list[ScannerStatus] = (
        cache.get("scanner_status", settings.CACHE_TTL_SECONDS) or []
    )
    if min_avg_return_pct > 0:
        ev_edges = [e for e in ev_edges if e.avg_return_pct >= min_avg_return_pct]
    return EvEdgesResponse(
        ev_edges=ev_edges[:limit],
        scanner_status=statuses,
        scanned_at=datetime.now(timezone.utc),
        total_markets=sum(s.markets_found for s in statuses),
    )


@app.get("/api/value", response_model=ValueResponse)
async def get_value_ops(
    min_cross_ev_pct: float = Query(2.0, ge=0, description="Min cross-market EV%"),
    limit:            int   = Query(100, ge=1, le=1000),
):
    """
    Return cached cross-market value opportunities: sportsbook bets that are
    mispriced relative to the Kalshi oracle probability for the same outcome.
    """
    value_ops: list[ValueOpportunity] = (
        cache.get("latest_value_ops", settings.CACHE_TTL_SECONDS) or []
    )
    statuses: list[ScannerStatus] = (
        cache.get("scanner_status", settings.CACHE_TTL_SECONDS) or []
    )
    if min_cross_ev_pct > 0:
        value_ops = [v for v in value_ops if v.cross_ev_pct >= min_cross_ev_pct]
    return ValueResponse(
        value_ops=value_ops[:limit],
        scanner_status=statuses,
        scanned_at=datetime.now(timezone.utc),
        total_markets=sum(s.markets_found for s in statuses),
    )


@app.get("/api/near-certainty")
async def get_near_certainty(
    min_prob: float = Query(97.0, ge=90, le=100, description="Minimum implied probability % (e.g. 97 = 97¢+)"),
    limit:    int   = Query(200, ge=1, le=1000),
):
    """
    Return all contracts currently priced at or above min_prob% implied probability.
    Results are sorted by price descending (highest certainty first).
    Any event type — sports, politics, crypto, economics, etc.
    """
    markets: list[NearCertaintyMarket] = (
        cache.get("near_certainty_markets", settings.CACHE_TTL_SECONDS) or []
    )
    min_price = min_prob / 100.0
    if min_price > 0.97:
        markets = [m for m in markets if m.price >= min_price]
    return {"markets": [m.model_dump(mode="json") for m in markets[:limit]], "count": len(markets)}


@app.post("/api/scan/trigger")
async def trigger_scan():
    """Manually trigger an immediate scan cycle."""
    asyncio.create_task(scan_and_broadcast())
    return {"message": "Scan triggered"}


# ── Sharp-Value endpoints (Odds API Pinnacle oracle, force-scan-only) ──────────

class SharpScanRequest(BaseModel):
    sport_keys: list[str] | None = None


@app.get("/api/sharp-value/sports")
async def get_sharp_value_sports():
    """
    Return metadata for all supported Pinnacle sport keys, enriched with the
    number of live Kalshi game contracts that match each sport.

    The Kalshi counts are derived from the latest scheduler scan (no extra API
    calls needed) and are cached up to 3 h.
    """
    from scanners.odds_api_pinnacle import PINNACLE_SPORT_KEYS, SPORT_KEY_META

    kalshi_counts: dict[str, int] = (
        cache.get("kalshi_sport_counts", 3600) or {}
    )

    # Kalshi sport slugs differ from Odds API keys: "nba", "nfl", "nhl", etc.
    # We derive the mapping from SPORT_KEY_META's group/label; fall back to a
    # hardcoded slug table for the most common keys.
    # Maps Odds API sport key → Kalshi parent_event_id prefix (first word).
    # Kalshi slugs come from scanners/kalshi.py SERIES_TO_SPORT.
    # Tennis all shares "tennis"; UCL/UEL not yet on Kalshi → empty string.
    ODDS_KEY_TO_KALSHI_SLUG: dict[str, str] = {
        "basketball_nba":            "nba",
        "basketball_ncaab":          "ncaab",
        "basketball_ncaaw":          "ncaaw",
        "basketball_wnba":           "wnba",
        "americanfootball_nfl":      "nfl",
        "americanfootball_ncaaf":    "ncaaf",
        "baseball_mlb":              "mlb",
        "icehockey_nhl":             "nhl",
        "soccer_usa_mls":            "mls",
        "soccer_epl":                "epl",
        "soccer_france_ligue_1":     "ligue1",
        "soccer_germany_bundesliga": "bundesliga",
        "soccer_spain_la_liga":      "laliga",
        "soccer_italy_serie_a":      "seriea",
        "soccer_uefa_champs_league": "",   # not yet on Kalshi
        "soccer_uefa_europa_league": "",   # not yet on Kalshi
        "tennis_atp_wimbledon":      "tennis",
        "tennis_atp_us_open":        "tennis",
        "tennis_atp_french_open":    "tennis",
        "tennis_atp_aus_open":       "tennis",
        "tennis_wta_wimbledon":      "tennis",
        "tennis_wta_us_open":        "tennis",
        "tennis_wta_french_open":    "tennis",
        "tennis_wta_aus_open":       "tennis",
        "mma_mixed_martial_arts":    "ufc",    # Kalshi slug is "ufc" (KXUFCFIGHT)
        "boxing_boxing":             "boxing",
    }

    result = []
    for key in PINNACLE_SPORT_KEYS:
        meta = SPORT_KEY_META.get(key, {"group": "Other", "label": key})
        kalshi_slug = ODDS_KEY_TO_KALSHI_SLUG.get(key, "")
        result.append({
            "sport_key":          key,
            "label":              meta["label"],
            "group":              meta["group"],
            "kalshi_slug":        kalshi_slug,
            "kalshi_event_count": kalshi_counts.get(kalshi_slug, 0),
        })

    return result


@app.get("/api/sharp-value")
async def get_sharp_value():
    """
    Return the cached result of the last force-triggered Pinnacle sharp-value scan.
    Returns empty arrays and default quota if no scan has been run yet.
    """
    from scanners.odds_api_pinnacle import load_usage
    usage    = load_usage()
    ops:     list[ValueOpportunity] = cache.get("sharp_value_ops",     86400) or []
    statuses: list[ScannerStatus]  = cache.get("sharp_value_statuses", 86400) or []
    last_at: str | None            = cache.get("sharp_value_last_at",  86400)
    return {
        "value_ops":          [v.model_dump(mode="json") for v in ops],
        "requests_remaining": usage["requests_remaining"],
        "requests_used":      usage["requests_used"],
        "monthly_limit":      usage["monthly_limit"],
        "last_scan_at":       last_at,
        "last_scan_cost":     usage["last_scan_cost"],
        "scanner_status":     [s.model_dump(mode="json") for s in statuses],
    }


@app.post("/api/sharp-value/scan")
async def trigger_sharp_value_scan(body: SharpScanRequest = SharpScanRequest()):
    """
    Force a Pinnacle sharp-value scan using the Odds API.

    This is the ONLY way scan_sharp_value() runs — it is never called by the
    90-s scheduler.  Each sport key costs 1 Odds API request; omitting
    sport_keys (or passing null) scans all supported sports.
    """
    if not settings.ODDS_API_KEY:
        raise HTTPException(
            status_code=400,
            detail="ODDS_API_KEY is not configured. Add it to backend/.env.",
        )

    from arbitrage.engine import scan_sharp_value
    from scanners.odds_api_pinnacle import load_usage

    n_keys = len(body.sport_keys) if body.sport_keys else "all"
    logger.info(f"[sharp_value] Force scan triggered by user ({n_keys} sport keys).")
    try:
        value_ops, statuses, requests_remaining = await scan_sharp_value(
            settings.ODDS_API_KEY, sport_keys=body.sport_keys
        )
    except Exception as exc:
        logger.error(f"[sharp_value] Scan failed: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))

    now_iso = datetime.now(timezone.utc).isoformat()
    cache.set("sharp_value_ops",      value_ops)
    cache.set("sharp_value_statuses", statuses)
    cache.set("sharp_value_last_at",  now_iso)

    usage = load_usage()

    result_payload = {
        "value_ops":          [v.model_dump(mode="json") for v in value_ops],
        "requests_remaining": requests_remaining,
        "requests_used":      usage.get("requests_used", 0),
        "monthly_limit":      500,
        "last_scan_at":       now_iso,
        "last_scan_cost":     usage.get("last_scan_cost", 0),
        "scanner_status":     [s.model_dump(mode="json") for s in statuses],
    }

    # Broadcast to all connected WebSocket clients so the tab updates in real-time
    ws_payload = json.dumps({
        "type": "sharp_value_update",
        "payload": result_payload,
    })
    dead: list = []
    for ws in list(active_connections):
        try:
            await ws.send_text(ws_payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        try:
            active_connections.remove(ws)
        except ValueError:
            pass

    logger.info(
        f"[sharp_value] Scan complete — {len(value_ops)} signals, "
        f"{requests_remaining} requests remaining."
    )
    return result_payload


@app.get("/api/sharp-value/usage")
async def get_sharp_value_usage():
    """Return current Odds API request quota stats."""
    from scanners.odds_api_pinnacle import load_usage
    return load_usage()


# ── Crypto endpoints (Kalshi 15M + daily markets) ──────────────────────────────

@app.get("/api/crypto")
async def get_crypto():
    """
    Return the cached crypto scan result (last scheduled scan).
    Markets are sorted: arbs first (highest net_profit_pct), then by close_time.
    """
    result: CryptoScanResult | None = cache.get("crypto_scan_result", settings.CACHE_TTL_SECONDS)
    if result is None:
        return {"markets": [], "arb_count": 0, "scanned_at": None}
    markets_sorted = sorted(
        result.markets,
        key=lambda m: (-m.net_profit_pct, m.close_time),
    )
    return {
        "markets":    [m.model_dump(mode="json") for m in markets_sorted],
        "arb_count":  result.arb_count,
        "scanned_at": result.scanned_at.isoformat(),
    }


@app.post("/api/crypto/scan")
async def trigger_crypto_scan():
    """
    Force a fresh Kalshi crypto scan (does not consume Odds API requests).
    Results are cached and broadcast to WebSocket clients.
    Returns the fresh scan result immediately.
    """
    import httpx as _httpx
    from scanners.kalshi_crypto import fetch_crypto_markets as _fetch

    try:
        async with _httpx.AsyncClient(timeout=25.0, follow_redirects=True) as client:
            crypto_result, _ = await _fetch(client)
    except Exception as exc:
        logger.error(f"[crypto] Force scan failed: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))

    cache.set("crypto_scan_result", crypto_result)

    # Broadcast to connected WebSocket clients
    ws_payload = json.dumps({
        "type": "crypto_update",
        "payload": {
            "crypto_markets":   [m.model_dump(mode="json") for m in crypto_result.markets],
            "crypto_arb_count": crypto_result.arb_count,
            "scanned_at":       crypto_result.scanned_at.isoformat(),
        },
    })
    dead: list = []
    for ws in list(active_connections):
        try:
            await ws.send_text(ws_payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        try:
            active_connections.remove(ws)
        except ValueError:
            pass

    markets_sorted = sorted(
        crypto_result.markets,
        key=lambda m: (-m.net_profit_pct, m.close_time),
    )
    return {
        "markets":    [m.model_dump(mode="json") for m in markets_sorted],
        "arb_count":  crypto_result.arb_count,
        "scanned_at": crypto_result.scanned_at.isoformat(),
    }


# ── WebSocket ──────────────────────────────────────────────────────────────────

@app.websocket("/ws/opportunities")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    active_connections.append(ws)
    logger.info(f"WebSocket client connected. Total: {len(active_connections)}")

    try:
        # Send current cached state immediately on connect
        opps: list[ArbitrageOpportunity] = (
            cache.get("latest_opportunities", settings.CACHE_TTL_SECONDS) or []
        )
        ev_edges: list[EvEdgeOpportunity] = (
            cache.get("latest_ev_edges", settings.CACHE_TTL_SECONDS) or []
        )
        value_ops: list[ValueOpportunity] = (
            cache.get("latest_value_ops", settings.CACHE_TTL_SECONDS) or []
        )
        statuses: list[ScannerStatus] = (
            cache.get("scanner_status", settings.CACHE_TTL_SECONDS) or []
        )
        sharp_ops: list[ValueOpportunity] = (
            cache.get("sharp_value_ops", 86400) or []
        )
        from scanners.odds_api_pinnacle import load_usage
        sharp_usage    = load_usage()
        sharp_last_at: str | None = cache.get("sharp_value_last_at", 86400)

        crypto_result: CryptoScanResult | None = cache.get("crypto_scan_result", settings.CACHE_TTL_SECONDS)
        crypto_markets_payload = (
            [m.model_dump(mode="json") for m in crypto_result.markets] if crypto_result else []
        )
        crypto_arb_count = crypto_result.arb_count if crypto_result else 0

        near_certainty: list[NearCertaintyMarket] = (
            cache.get("near_certainty_markets", settings.CACHE_TTL_SECONDS) or []
        )

        await ws.send_text(json.dumps({
            "type": "opportunities_update",
            "payload": {
                "opportunities":         [o.model_dump(mode="json") for o in opps],
                "ev_edges":              [e.model_dump(mode="json") for e in ev_edges],
                "value_ops":             [v.model_dump(mode="json") for v in value_ops],
                "scanner_status":        [s.model_dump(mode="json") for s in statuses],
                "count":                 len(opps),
                "ev_edges_count":        len(ev_edges),
                "value_ops_count":       len(value_ops),
                # Sharp-value initial state
                "sharp_value_ops":       [v.model_dump(mode="json") for v in sharp_ops],
                "sharp_value_remaining": sharp_usage["requests_remaining"],
                "sharp_value_last_scan_at": sharp_last_at,
                # Crypto initial state
                "crypto_markets":        crypto_markets_payload,
                "crypto_arb_count":      crypto_arb_count,
                # Near-certainty initial state
                "near_certainty":        [m.model_dump(mode="json") for m in near_certainty],
                "near_certainty_count":  len(near_certainty),
            },
        }))

        # Keep connection alive; client may send pings
        while True:
            await ws.receive_text()

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.warning(f"WebSocket error: {exc}")
    finally:
        try:
            active_connections.remove(ws)
        except ValueError:
            pass
        logger.info(f"WebSocket client disconnected. Total: {len(active_connections)}")
