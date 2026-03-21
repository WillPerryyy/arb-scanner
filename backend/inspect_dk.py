"""Inspect DraftKings NBA teams to understand name format."""
import asyncio
import httpx

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Referer": "https://www.actionnetwork.com/",
}

async def main():
    async with httpx.AsyncClient(timeout=25.0) as client:
        resp = await client.get(
            "https://api.actionnetwork.com/web/v1/scoreboard/nba?periods=full&bookIds=15",
            headers=HEADERS,
        )
        data = resp.json()
        games = data.get("games", [])
        print(f"NBA games today: {len(games)}")
        for game in games:
            teams = game.get("teams", [])
            home_id = game.get("home_team_id")
            away_id = game.get("away_team_id")
            teams_by_id = {t.get("id"): t for t in teams}
            home = teams_by_id.get(home_id, teams[0] if teams else {})
            away = teams_by_id.get(away_id, teams[1] if len(teams) > 1 else {})
            print(f"\nGame: {away.get('full_name')} @ {home.get('full_name')}")
            print(f"  home full_name:  {home.get('full_name')}")
            print(f"  home short_name: {home.get('short_name')}")
            print(f"  home abbr:       {home.get('abbr')}")
            print(f"  away full_name:  {away.get('full_name')}")
            print(f"  away short_name: {away.get('short_name')}")
            print(f"  away abbr:       {away.get('abbr')}")
            # Check available odds
            odds_list = game.get("odds", [])
            for odds in odds_list:
                ml_home = odds.get("ml_home")
                ml_away = odds.get("ml_away")
                if ml_home and ml_away:
                    print(f"  DK odds: home {ml_home} / away {ml_away}")

asyncio.run(main())
