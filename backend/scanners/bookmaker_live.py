"""
Bookmaker.eu live odds scraper — Selenium + undetected-chromedriver.

Standalone: does NOT extend BaseScanner, NOT in ALL_SCANNER_CLASSES.

Why standalone?
  Same reason as pinnacle_live.py — Selenium is synchronous and this scraper
  is designed to run on a cron/manual schedule, not inside the FastAPI event loop.

Why simpler than pinnacle_live.py?
  Bookmaker.eu (lines.bookmaker.eu) is a **server-rendered HTML** site, not a React
  SPA.  Odds are present in the initial page response — no CDP XHR interception,
  no fetch() override, no window.__xhrData__ required.  Standard Selenium DOM reads
  with element IDs are sufficient.

DOM ID convention (N = game number, 1-indexed, contiguous):
  #vN_N   — away team name      e.g. "Cleveland Cavaliers"
  #hN_N   — home team name      e.g. "Brooklyn Nets"
  #vM_N   — away moneyline      e.g. "+930"
  #hM_N   — home moneyline      e.g. "-2594"
  #Game{N}_Time  — game time    e.g. "3/01 12:42pm PT"

Iteration: loop N=1,2,3... until NoSuchElementException on #vN_{N} → done.

Output: backend/data/bookmaker_live.json
  {
    "scraped_at": "<ISO>",
    "count": N,
    "contracts": [
      {
        "platform":          "bookmaker_live",
        "market_id":         "nba bos dal_home",   ← stable, cross-scrape consistent
        "parent_event_id":   "nba bos dal",         ← matches Kalshi/DK namespace
        "outcome_label":     "BOS",
        "is_yes_side":       true,
        "price":             0.476190,              ← 1/decimal_odds
        "decimal_odds":      2.1,
        "american_odds":     110,
        ...
      }
    ]
  }

Team name → abbreviation:
  Uses TEAM_ABBR_MAP from odds_api.py (150+ full names already mapped for NBA/NFL/MLB/NHL/MLS).
  Falls back to _abbr() (first 3 uppercase letters of last word) for anything not in the map.

Namespace alignment:
  parent_event_id = normalize_event_key(f"{sport} {home_abbr} {away_abbr}")
  This matches the format produced by Action Network scanners → Kalshi/DK contracts will key-match.

Geo-note:
  Bookmaker.eu accepts US players (offshore) — no VPN required.
  The site does not use heavy Cloudflare bot detection, but we still use
  undetected-chromedriver for safety.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import undetected_chromedriver as uc
    from selenium.webdriver.common.by import By
    from selenium.common.exceptions import NoSuchElementException
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "Selenium dependencies not installed. "
        "Run: pip install selenium undetected-chromedriver webdriver-manager setuptools"
    ) from exc

# Reuse existing project utilities — no duplication
try:
    from arbitrage.matcher import normalize_event_key
    from scanners.odds_api import TEAM_ABBR_MAP, american_to_decimal
except ImportError:
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from arbitrage.matcher import normalize_event_key
    from scanners.odds_api import TEAM_ABBR_MAP, american_to_decimal

logger = logging.getLogger(__name__)

# ── Output ─────────────────────────────────────────────────────────────────────
OUTPUT_PATH = Path(__file__).parent.parent / "data" / "bookmaker_live.json"

# ── Sport pages ────────────────────────────────────────────────────────────────
# Maps Bookmaker.eu URL → Action Network sport slug (for parent_event_id namespace).
# www.bookmaker.eu → 301 → lines.bookmaker.eu; use the final host directly.
SPORT_PAGES: dict[str, str] = {
    "https://lines.bookmaker.eu/en/sports/basketball/nba":                "nba",
    "https://lines.bookmaker.eu/en/sports/ice-hockey/nhl":               "nhl",
    "https://lines.bookmaker.eu/en/sports/baseball/mlb":                 "mlb",
    "https://lines.bookmaker.eu/en/sports/soccer/usa-mls":               "mls",
    "https://lines.bookmaker.eu/en/sports/soccer/england-premier-league": "epl",
    "https://lines.bookmaker.eu/en/sports/soccer/spain-la-liga":         "laliga",
    "https://lines.bookmaker.eu/en/sports/soccer/germany-bundesliga":    "bundesliga",
    "https://lines.bookmaker.eu/en/sports/soccer/france-ligue-1":        "ligue1",
    "https://lines.bookmaker.eu/en/sports/soccer/italy-serie-a":         "seriea",
}

# Sports where a draw is a valid third outcome (affects num_outcomes and future matching)
SOCCER_SPORTS: frozenset[str] = frozenset({
    "mls", "epl", "laliga", "bundesliga", "ligue1", "seriea", "soccer",
})

# Seconds to wait after driver.get() for the page to fully render
PAGE_SETTLE_SECONDS = 4


class BookmakerLiveScraper:
    """
    Standalone Selenium scraper for Bookmaker.eu live moneyline odds.

    Usage (headless, default):
        scraper = BookmakerLiveScraper()
        contracts = scraper.scrape_all()
        scraper.save(contracts)
        scraper.close()

    Usage (visible window — for debugging):
        scraper = BookmakerLiveScraper(headless=False)
        ...

    Context manager usage:
        with BookmakerLiveScraper() as scraper:
            contracts = scraper.scrape_all()
            scraper.save(contracts)
    """

    def __init__(self, headless: bool = True):
        options = uc.ChromeOptions()
        options.add_argument("--window-size=1400,900")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        if headless:
            options.add_argument("--headless=new")

        logger.info("[bookmaker_live] Launching Chrome...")
        self.driver = _init_driver(options)
        logger.info("[bookmaker_live] Chrome ready.")

    # ── Context manager ────────────────────────────────────────────────────────

    def __enter__(self) -> "BookmakerLiveScraper":
        return self

    def __exit__(self, *_) -> None:
        self.close()

    # ── Per-sport scraping ─────────────────────────────────────────────────────

    def scrape_sport(self, url: str, sport: str) -> list[dict]:
        """
        Navigate to a Bookmaker.eu sport page and extract moneyline contracts.
        Returns a list of MarketContract-schema dicts.

        Because the page is server-rendered, we only need to:
          1. Navigate to the URL
          2. Wait a few seconds for any JS progressive-enhancement to settle
          3. Iterate DOM element IDs #vN_1, #hN_1, #vM_1, #hM_1 ... until gone
        """
        logger.info(f"[bookmaker_live] {sport.upper()} — {url}")
        try:
            self.driver.get(url)
        except Exception as exc:
            logger.error(f"[bookmaker_live] Navigation failed for {sport}: {exc}")
            return []

        time.sleep(PAGE_SETTLE_SECONDS)

        # Quick sanity check — Bookmaker doesn't geo-block US users but
        # may show a login wall or maintenance page occasionally.
        page_title = self.driver.title or ""
        if any(kw in page_title.lower() for kw in ("403", "blocked", "maintenance", "error")):
            logger.warning(
                f"[bookmaker_live] {sport} — suspicious page title: '{page_title}'. "
                f"Run with --visible to inspect."
            )

        contracts = self._scrape_game_rows(sport)
        logger.info(f"[bookmaker_live] {sport}: {len(contracts)} contracts extracted")
        return contracts

    # ── DOM iteration ──────────────────────────────────────────────────────────

    def _scrape_game_rows(self, sport: str) -> list[dict]:
        """
        Iterate game rows by ID (N=1,2,3...) until NoSuchElementException.

        Element IDs per game N:
          #vN_{N}        — away team full name
          #hN_{N}        — home team full name
          #vM_{N}        — away moneyline text (+930, -145, OFF, PK, ...)
          #hM_{N}        — home moneyline text
          #Game{N}_Time  — game time string (optional, doesn't fail if absent)
        """
        contracts: list[dict] = []
        now_iso = datetime.now(timezone.utc).isoformat()
        is_soccer = sport in SOCCER_SPORTS
        n_outcomes = 3 if is_soccer else 2

        n = 1
        while True:
            # ── Team names ─────────────────────────────────────────────────────
            try:
                away_name = self.driver.find_element(By.ID, f"vN_{n}").text.strip()
                home_name = self.driver.find_element(By.ID, f"hN_{n}").text.strip()
            except NoSuchElementException:
                break  # no game N → all games parsed, exit cleanly

            # ── Moneyline text ─────────────────────────────────────────────────
            away_ml_text = ""
            home_ml_text = ""
            try:
                away_ml_text = self.driver.find_element(By.ID, f"vM_{n}").text.strip()
                home_ml_text = self.driver.find_element(By.ID, f"hM_{n}").text.strip()
            except NoSuchElementException:
                pass  # moneyline columns missing — skip this game

            # ── Game time (optional) ───────────────────────────────────────────
            start_time: str | None = None
            try:
                start_time = self.driver.find_element(By.ID, f"Game{n}_Time").text.strip() or None
            except NoSuchElementException:
                pass

            # ── Parse moneylines ───────────────────────────────────────────────
            ml_away = _parse_moneyline(away_ml_text)
            ml_home = _parse_moneyline(home_ml_text)

            if ml_away is None or ml_home is None:
                logger.debug(
                    f"[bookmaker_live] Game {n} ({away_name} @ {home_name}): "
                    f"no line (away='{away_ml_text}', home='{home_ml_text}') — skipped"
                )
                n += 1
                continue

            # ── Abbreviations ──────────────────────────────────────────────────
            # TEAM_ABBR_MAP keys are lowercase full names from odds_api.py
            home_abbr = TEAM_ABBR_MAP.get(home_name.lower(), _abbr(home_name)).upper()
            away_abbr = TEAM_ABBR_MAP.get(away_name.lower(), _abbr(away_name)).upper()

            # ── Namespace alignment ────────────────────────────────────────────
            # normalize_event_key produces the same canonical key as Action Network scanners
            parent_event_id = normalize_event_key(f"{sport} {home_abbr} {away_abbr}")
            game_title = f"{away_name} @ {home_name}"

            dec_home = american_to_decimal(ml_home)
            dec_away = american_to_decimal(ml_away)

            # ── Two contracts per game (home win + away win) ───────────────────
            # market_id uses parent_event_id so it's stable across scrape runs
            # and cross-platform meaningful (unlike Bookmaker's internal row number N)
            contracts.append(_make_contract(
                platform="bookmaker_live",
                market_id=f"{parent_event_id}_home",
                parent_event_id=parent_event_id,
                game_title=game_title,
                abbr=home_abbr,
                team_name=home_name,
                is_yes=True,
                decimal_odds=dec_home,
                american_odds=ml_home,
                sport=sport,
                start_time=start_time,
                now_iso=now_iso,
                n_outcomes=n_outcomes,
            ))
            contracts.append(_make_contract(
                platform="bookmaker_live",
                market_id=f"{parent_event_id}_away",
                parent_event_id=parent_event_id,
                game_title=game_title,
                abbr=away_abbr,
                team_name=away_name,
                is_yes=False,
                decimal_odds=dec_away,
                american_odds=ml_away,
                sport=sport,
                start_time=start_time,
                now_iso=now_iso,
                n_outcomes=n_outcomes,
            ))

            n += 1

        return contracts

    # ── Top-level orchestration ────────────────────────────────────────────────

    def scrape_all(self) -> list[dict]:
        """
        Iterate over all SPORT_PAGES and collect contracts.
        Returns the combined list; logs a summary at the end.
        """
        all_contracts: list[dict] = []
        for url, sport in SPORT_PAGES.items():
            try:
                contracts = self.scrape_sport(url, sport)
                all_contracts.extend(contracts)
            except Exception as exc:
                logger.error(f"[bookmaker_live] {sport} scrape failed: {exc}")

        logger.info(
            f"[bookmaker_live] Total: {len(all_contracts)} contracts "
            f"from {len(SPORT_PAGES)} sport pages."
        )
        return all_contracts

    # ── Persistence ────────────────────────────────────────────────────────────

    def save(self, contracts: list[dict]) -> Path:
        """
        Write contracts to OUTPUT_PATH as JSON.
        Creates the parent data/ directory if needed.
        Returns the path written.
        """
        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "scraped_at": datetime.now(timezone.utc).isoformat(),
            "count":      len(contracts),
            "contracts":  contracts,
        }
        with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        logger.info(f"[bookmaker_live] Saved {len(contracts)} contracts → {OUTPUT_PATH}")
        return OUTPUT_PATH

    # ── Cleanup ────────────────────────────────────────────────────────────────

    def close(self) -> None:
        """Quit the browser. Safe to call multiple times."""
        try:
            self.driver.quit()
            logger.info("[bookmaker_live] Browser closed.")
        except Exception:
            pass


# ── Module-level helpers ───────────────────────────────────────────────────────

def _parse_moneyline(text: str) -> int | None:
    """
    Parse a Bookmaker.eu moneyline cell text into an integer American odds value.

    Handles:
      "+930"     → 930
      "-2594"    → -2594
      "-145"     → -145
      "PK"       → -110   (pick'em: both sides even, conventional -110 vig)
      "PK-"      → -110
      "+PK"      → -110
      "EVN"      → -110   (alternate even-money notation)
      "OFF"      → None   (no line available)
      "N/A"      → None
      "" or "-"  → None
    """
    t = text.strip().upper().replace(" ", "")
    if not t or t in ("OFF", "N/A", "-", "—", "EVEN"):
        return None
    if t in ("PK", "PK-", "+PK", "EVN"):
        return -110
    try:
        return int(t.replace("+", ""))
    except ValueError:
        logger.debug(f"[bookmaker_live] Unparseable moneyline text: '{text}'")
        return None


def _init_driver(options: uc.ChromeOptions) -> uc.Chrome:
    """
    Create an undetected-chromedriver Chrome instance with the correct
    ChromeDriver version.

    Strategy:
      1. Detect installed Chrome major version (e.g. 145)
      2. Use webdriver-manager to download/cache ChromeDriver matching that version
      3. Pass the cached binary path to uc.Chrome() via driver_executable_path

    Why this avoids the patcher download issue:
      uc.Chrome() with driver_executable_path skips its own fetch_release_number()
      network call (which hits googlechromelabs.github.io and can fail on rapid
      re-invocations due to rate-limiting or CDN resets).  webdriver-manager
      caches the binary locally, so subsequent runs are purely offline.
    """
    from webdriver_manager.chrome import ChromeDriverManager

    chrome_major = _detect_chrome_major()
    driver_kwargs: dict = {"options": options}

    try:
        if chrome_major:
            logger.info(f"[bookmaker_live] Chrome major: {chrome_major} — fetching matching ChromeDriver via webdriver-manager...")
            drv_path = ChromeDriverManager(driver_version=f"LATEST_RELEASE_{chrome_major}").install()
        else:
            logger.info("[bookmaker_live] Chrome version unknown — using webdriver-manager auto-detect...")
            drv_path = ChromeDriverManager().install()

        driver_kwargs["driver_executable_path"] = drv_path
        if chrome_major:
            driver_kwargs["version_main"] = chrome_major
        logger.debug(f"[bookmaker_live] ChromeDriver path: {drv_path}")
    except Exception as exc:
        # Fallback: let uc.Chrome auto-download (original behaviour)
        logger.warning(
            f"[bookmaker_live] webdriver-manager failed ({exc}); "
            f"falling back to uc auto-download."
        )
        if chrome_major:
            driver_kwargs["version_main"] = chrome_major

    return uc.Chrome(**driver_kwargs)


def _detect_chrome_major() -> int | None:
    """
    Return the installed Chrome major version number (e.g. 145) so we can
    pass `version_main` to uc.Chrome() and avoid ChromeDriver version mismatches.

    Strategy:
      Windows — Chrome is a GUI app; `chrome.exe --version` produces no stdout.
                Read the file version metadata via PowerShell instead.
      Unix/Mac — Use `chrome --version` which writes to stdout normally.

    Returns None if detection fails — uc.Chrome() will guess automatically.
    """
    import os
    import subprocess
    import sys

    win_candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    ]
    unix_candidates = [
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/chromium-browser",
        "/usr/bin/chromium",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]

    if sys.platform == "win32":
        for path in win_candidates:
            if not os.path.exists(path):
                continue
            try:
                # PowerShell VersionInfo is the reliable way to read Windows EXE versions
                ps_cmd = f"(Get-Item '{path}').VersionInfo.ProductVersion"
                out = subprocess.check_output(
                    ["powershell", "-NoProfile", "-Command", ps_cmd],
                    stderr=subprocess.DEVNULL,
                    timeout=8,
                ).decode().strip()
                major = int(out.split(".")[0])
                return major
            except Exception:
                continue
    else:
        for path in unix_candidates:
            if not os.path.exists(path):
                continue
            try:
                out = subprocess.check_output(
                    [path, "--version"], stderr=subprocess.DEVNULL, timeout=5
                ).decode().strip()
                # "Google Chrome 145.0.7632.117"
                major = int(out.split()[-1].split(".")[0])
                return major
            except Exception:
                continue
    return None


def _abbr(name: str) -> str:
    """
    Fallback abbreviation when the team name is not in TEAM_ABBR_MAP.
    Takes the first 4 uppercase letters of the last word in the name.
    E.g. "Portland Trail Blazers" → "BLAZ", "Utah Jazz" → "JAZZ".
    """
    word = name.strip().split()[-1] if name.strip() else name
    return word[:4].upper()


def _make_contract(
    *,
    platform: str,
    market_id: str,
    parent_event_id: str,
    game_title: str,
    abbr: str,
    team_name: str,
    is_yes: bool,
    decimal_odds: float,
    american_odds: int,
    sport: str,
    start_time: str | None,
    now_iso: str,
    n_outcomes: int,
) -> dict:
    """
    Build a single MarketContract-schema dict.

    Field names match MarketContract exactly so future wiring is:
        MarketContract(**contract_dict)
    Extra metadata fields (american_odds, sport, start_time, scraped_at)
    are ignored by MarketContract's Pydantic model but kept for human
    inspection of the JSON output file.
    """
    return {
        # ── Core MarketContract fields ──────────────────────────────────────
        "platform":             platform,
        "market_id":            market_id,
        "parent_event_id":      parent_event_id,
        "parent_event_title":   game_title,
        "outcome_label":        abbr,
        "is_yes_side":          is_yes,
        "event_title":          f"{game_title} — {abbr} to win [Bookmaker]",
        "side":                 "yes" if is_yes else "no",
        "price":                round(1.0 / decimal_odds, 6),
        "payout_per_contract":  1.0,
        "decimal_odds":         round(decimal_odds, 6),
        "num_outcomes":         n_outcomes,
        # ── Extra metadata ──────────────────────────────────────────────────
        "american_odds":        american_odds,
        "sport":                sport,
        "start_time":           start_time,
        "scraped_at":           now_iso,
    }
