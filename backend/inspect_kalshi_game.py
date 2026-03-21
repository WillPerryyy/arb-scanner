"""
Inspect Kalshi game events structure - specifically looking for NCAAB/NBA game events.
"""
import asyncio
import httpx
import re
import json

BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"

def _is_game_title(title: str) -> bool:
    return bool(re.search(r"\s+(?:vs\.?|@)\s+", title, re.IGNORECASE))

async def main():
    async with httpx.AsyncClient(timeout=25.0) as client:
        # Directly fetch KXNCAABGAME events
        for series in ["KXNCAABGAME", "KXNBAGAME", "KXNHLGAME", "KXMLBGAME", "KXNFLGAME",
                       "KXMLS", "KXUFC", "KXNBA", "KXNCAAB", "KXSOC"]:
            resp = await client.get(
                f"{BASE_URL}/events",
                params={
                    "limit": 5,
                    "with_nested_markets": "true",
                    "status": "open",
                    "series_ticker": series,
                },
            )
            data = resp.json()
            events = data.get("events", [])
            if not events:
                print(f"[{series}] No events found")
                continue

            print(f"[{series}] {len(events)} events:")
            for event in events[:3]:
                title = event.get("title", "")
                ticker = event.get("event_ticker", "")
                markets = event.get("markets", [])
                print(f"  EVENT: {title}")
                print(f"  ticker: {ticker}")
                print(f"  game_title: {_is_game_title(title)}")
                print(f"  num_markets: {len(markets)}")
                for i, m in enumerate(markets[:4]):
                    print(f"    Market {i}:")
                    print(f"      ticker:     {m.get('ticker')}")
                    print(f"      title:      {m.get('title')}")
                    print(f"      subtitle:   {repr(m.get('subtitle'))}")
                    print(f"      yes_ask:    {m.get('yes_ask')}")
                    print(f"      no_ask:     {m.get('no_ask')}")
                print()
            print("---")

asyncio.run(main())
