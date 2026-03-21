"""
Cross-check: do DraftKings abbr fields match Kalshi's ticker suffixes for the same games?
"""
import asyncio
import httpx
import re

BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "application/json",
    "Referer": "https://www.actionnetwork.com/",
}

async def main():
    async with httpx.AsyncClient(timeout=25.0) as client:
        # Get DK NBA games
        dk_resp = await client.get(
            "https://api.actionnetwork.com/web/v1/scoreboard/nba?periods=full&bookIds=15",
            headers=HEADERS,
        )
        dk_data = dk_resp.json()
        dk_games = dk_data.get("games", [])
        print(f"DK NBA games: {len(dk_games)}")
        print("\nDK abbreviations (abbr field):")
        dk_abbrs = {}
        for game in dk_games:
            teams = game.get("teams", [])
            for t in teams:
                print(f"  {t.get('full_name'):30s} abbr={t.get('abbr')} short={t.get('short_name')}")
                dk_abbrs[t.get("abbr", "")] = t.get("short_name", t.get("full_name", ""))

        # Get Kalshi NBA game events
        print("\nKalshi NBA game ticker suffixes:")
        kalshi_resp = await client.get(
            f"{BASE_URL}/events",
            params={"limit": 50, "with_nested_markets": "true", "status": "open", "series_ticker": "KXNBAGAME"},
        )
        kalshi_data = kalshi_resp.json()
        kalshi_events = kalshi_data.get("events", [])
        kalshi_abbrs = {}
        for event in kalshi_events:
            title = event.get("title", "")
            markets = event.get("markets", [])
            if len(markets) == 2:
                for m in markets:
                    ticker = m.get("ticker", "")
                    suffix = ticker.rsplit("-", 1)[-1] if "-" in ticker else ""
                    print(f"  {title:40s} ticker_suffix={suffix}")
                    kalshi_abbrs[suffix] = title

        print("\n--- CROSS-CHECK ---")
        print("DK abbrs that match Kalshi suffixes:")
        for abbr in sorted(dk_abbrs.keys()):
            if abbr in kalshi_abbrs:
                print(f"  {abbr:5s} → DK: {dk_abbrs[abbr]:20s} | Kalshi: {kalshi_abbrs[abbr]}")
            else:
                print(f"  {abbr:5s} → DK: {dk_abbrs[abbr]:20s} | NOT IN KALSHI")

        print("\nKalshi suffixes not in DK:")
        for suffix in sorted(kalshi_abbrs.keys()):
            if suffix not in dk_abbrs:
                print(f"  {suffix:5s} in game: {kalshi_abbrs[suffix]}")

        # Also check NHL
        print("\n\n=== NHL ===")
        dk_nhl = await client.get(
            "https://api.actionnetwork.com/web/v1/scoreboard/nhl?periods=full&bookIds=15",
            headers=HEADERS,
        )
        dk_nhl_data = dk_nhl.json()
        dk_nhl_games = dk_nhl_data.get("games", [])
        print(f"DK NHL games: {len(dk_nhl_games)}")
        dk_nhl_abbrs = {}
        for game in dk_nhl_games:
            for t in game.get("teams", []):
                print(f"  {t.get('full_name'):30s} abbr={t.get('abbr')} short={t.get('short_name')}")
                dk_nhl_abbrs[t.get("abbr", "")] = t.get("short_name", "")

        kalshi_nhl = await client.get(
            f"{BASE_URL}/events",
            params={"limit": 50, "with_nested_markets": "true", "status": "open", "series_ticker": "KXNHLGAME"},
        )
        kalshi_nhl_data = kalshi_nhl.json()
        kalshi_nhl_abbrs = {}
        print("\nKalshi NHL game ticker suffixes:")
        for event in kalshi_nhl_data.get("events", []):
            title = event.get("title", "")
            for m in event.get("markets", []):
                ticker = m.get("ticker", "")
                suffix = ticker.rsplit("-", 1)[-1] if "-" in ticker else ""
                print(f"  {title:40s} suffix={suffix}")
                kalshi_nhl_abbrs[suffix] = title

        print("\nNHL cross-check:")
        for abbr in sorted(dk_nhl_abbrs.keys()):
            match = abbr in kalshi_nhl_abbrs
            print(f"  DK: {abbr:5s} ({dk_nhl_abbrs[abbr]:20s}) | Kalshi: {'MATCH' if match else 'NO MATCH'}")

asyncio.run(main())
