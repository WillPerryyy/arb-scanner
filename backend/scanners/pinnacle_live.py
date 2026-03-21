"""
Pinnacle live odds scraper — Selenium + undetected-chromedriver.

Standalone: does NOT extend BaseScanner, NOT in ALL_SCANNER_CLASSES.

Why standalone?
  BaseScanner.fetch_markets() is async (backed by httpx.AsyncClient).
  Selenium is fully synchronous.  Mixing them requires run_in_executor
  boilerplate that adds complexity for no benefit — we want this scraper
  to run on a cron/manual schedule, not inside the FastAPI event loop.

Output: backend/data/pinnacle_live.json
  Top-level schema:
    { "scraped_at": "<ISO>", "count": N, "contracts": [ ... ] }
  Each contract dict uses exact MarketContract field names so future
  integration is simply: MarketContract(**contract_dict)

Network strategy — XHR interception via CDP:
  Pinnacle is a React SPA.  Odds come from Pinnacle's internal Arcadia API:
    https://guest.api.arcadia.pinnacle.com/0.1/leagues/{id}/matchups
  We inject a fetch() override via Page.addScriptToEvaluateOnNewDocument
  (Chrome DevTools Protocol) so the override is in place before the page's
  own scripts run.  Captured JSON is stored in window.__pinnacleXHRData__
  and retrieved after the page settles.

parent_event_id namespace:
  Uses normalize_event_key() with the same sport prefix as Action Network
  scanners: normalize_event_key("nba NYK SAS") → "nba nyk sas".
  This ensures Pinnacle live contracts will key-match Kalshi and DK contracts
  once integration is wired.

Arcadia matchup JSON shape (2024–2025):
  [
    { "id": 1234567,
      "startTime": "2025-03-01T23:05:00Z",
      "participants": [
        { "alignment": "home", "name": "Boston Celtics",
          "shortName": "BOS" },
        { "alignment": "away", "name": "Dallas Mavericks",
          "shortName": "DAL" },
      ],
      "prices": [
        { "designation": "home", "price": -210, "points": 0 },
        { "designation": "away", "price": +175, "points": 0 },
      ],
      ...
    },
    ...
  ]

Geo-note:
  Pinnacle blocks US IP addresses (geo-restriction).  You need a VPN set to
  a jurisdiction where Pinnacle operates (e.g. Canada, Malta) for the scraper
  to load real odds rather than a "not available in your region" page.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import undetected_chromedriver as uc
    from selenium.webdriver.support.ui import WebDriverWait
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "Selenium dependencies not installed. "
        "Run: pip install selenium undetected-chromedriver webdriver-manager"
    ) from exc

# normalize_event_key is project-internal — adjust the import path if you
# move this file outside the backend/ tree.
try:
    from arbitrage.matcher import normalize_event_key
except ImportError:
    # Fallback for running the file directly outside the package
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from arbitrage.matcher import normalize_event_key

logger = logging.getLogger(__name__)

# ── Output path ────────────────────────────────────────────────────────────────
OUTPUT_PATH = Path(__file__).parent.parent / "data" / "pinnacle_live.json"

# ── Sport pages ────────────────────────────────────────────────────────────────
# Maps Pinnacle URL → Action Network sport slug (for parent_event_id namespace).
# Add more URLs here as needed; the slug MUST match AN_SPORTS in action_network.py.
SPORT_PAGES: dict[str, str] = {
    "https://www.pinnacle.com/en/basketball/nba/matchups/":                  "nba",
    "https://www.pinnacle.com/en/hockey/nhl/matchups/":                      "nhl",
    "https://www.pinnacle.com/en/baseball/major-league-baseball/matchups/":  "mlb",
    "https://www.pinnacle.com/en/football/nfl/matchups/":                    "nfl",
    "https://www.pinnacle.com/en/soccer/epl/matchups/":                      "epl",
    "https://www.pinnacle.com/en/soccer/mls/matchups/":                      "mls",
    "https://www.pinnacle.com/en/tennis/matchups/":                          "tennis",
}

# Arcadia host — used inside the injected JS to filter relevant fetch calls
ARCADIA_HOST = "arcadia.pinnacle.com"

# Seconds to wait after page load for XHR data to arrive
PAGE_SETTLE_SECONDS = 8


# ── CDP fetch-interceptor script ───────────────────────────────────────────────
# Injected before the page's own scripts run via addScriptToEvaluateOnNewDocument.
# Wraps window.fetch so every response to an Arcadia matchups endpoint is cloned
# and its parsed JSON body pushed into window.__pinnacleXHRData__.
_FETCH_INTERCEPTOR = f"""
(function() {{
    window.__pinnacleXHRData__ = [];
    const _origFetch = window.fetch;
    window.fetch = async function(...args) {{
        const resp = await _origFetch.apply(this, args);
        try {{
            const url = (typeof args[0] === 'string') ? args[0]
                        : (args[0] && args[0].url) ? args[0].url
                        : '';
            if (url.includes('{ARCADIA_HOST}') && url.includes('matchups')) {{
                const clone = resp.clone();
                clone.json().then(function(data) {{
                    window.__pinnacleXHRData__.push({{ url: url, data: data }});
                }}).catch(function() {{}});
            }}
        }} catch(e) {{}}
        return resp;
    }};
}})();
"""


class PinnacleLiveScraper:
    """
    Standalone Selenium scraper for Pinnacle live moneyline odds.

    Usage (headless, default):
        scraper = PinnacleLiveScraper()
        contracts = scraper.scrape_all()
        scraper.save(contracts)
        scraper.close()

    Usage (visible window — for debugging):
        scraper = PinnacleLiveScraper(headless=False)
        ...

    Context manager usage:
        with PinnacleLiveScraper() as scraper:
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

        logger.info("[pinnacle_live] Launching Chrome...")
        self.driver = _init_driver(options)
        self._enable_network_capture()
        logger.info("[pinnacle_live] Chrome ready.")

    # ── Context manager support ────────────────────────────────────────────────

    def __enter__(self) -> "PinnacleLiveScraper":
        return self

    def __exit__(self, *_) -> None:
        self.close()

    # ── Setup ──────────────────────────────────────────────────────────────────

    def _enable_network_capture(self) -> None:
        """Enable CDP Network domain (required for fetch interception)."""
        self.driver.execute_cdp_cmd("Network.enable", {})

    def _inject_fetch_interceptor(self) -> None:
        """
        Register the fetch() override so it fires before the page's own scripts.
        Must be called before every driver.get() call because
        addScriptToEvaluateOnNewDocument persists across navigations but the
        previous page's window.__pinnacleXHRData__ is destroyed on navigation.
        """
        self.driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": _FETCH_INTERCEPTOR},
        )

    # ── Per-sport scraping ─────────────────────────────────────────────────────

    def scrape_sport(self, url: str, sport: str) -> list[dict]:
        """
        Navigate to a Pinnacle sport matchups page and extract moneyline
        contracts.  Returns a list of MarketContract-schema dicts.

        Steps:
          1. Register fetch interceptor (before navigation)
          2. Navigate — React app loads and fires XHR to Arcadia
          3. Wait PAGE_SETTLE_SECONDS for async data to arrive
          4. Pull window.__pinnacleXHRData__ from the page context
          5. Parse each captured matchups response
        """
        logger.info(f"[pinnacle_live] {sport.upper()} — {url}")
        self._inject_fetch_interceptor()

        try:
            self.driver.get(url)
        except Exception as exc:
            logger.error(f"[pinnacle_live] Navigation failed for {sport}: {exc}")
            return []

        # Wait for the page + XHR responses to settle
        logger.debug(f"[pinnacle_live] Waiting {PAGE_SETTLE_SECONDS}s for {sport}...")
        time.sleep(PAGE_SETTLE_SECONDS)

        # Check for geo-block / Cloudflare challenge
        page_title = self.driver.title or ""
        body_snippet = ""
        try:
            body_el = self.driver.find_element("tag name", "body")
            body_snippet = (body_el.text or "")[:200].lower()
        except Exception:
            pass

        if any(kw in body_snippet for kw in ("not available", "access denied", "403", "region")):
            logger.warning(
                f"[pinnacle_live] {sport} — possible geo-block or Cloudflare challenge. "
                f"Title: '{page_title}'. Try running with a VPN or --visible to inspect."
            )

        # Retrieve captured XHR data
        raw_entries: list[dict] = []
        try:
            raw_entries = self.driver.execute_script(
                "return window.__pinnacleXHRData__ || [];"
            ) or []
        except Exception as exc:
            logger.warning(f"[pinnacle_live] Could not read XHR data for {sport}: {exc}")

        if not raw_entries:
            logger.warning(
                f"[pinnacle_live] No Arcadia XHR data captured for {sport}. "
                f"The page may be geo-blocked or the API URL pattern changed."
            )
            return []

        logger.debug(f"[pinnacle_live] {sport}: {len(raw_entries)} XHR response(s) captured")

        contracts: list[dict] = []
        for entry in raw_entries:
            try:
                parsed = self._parse_matchups(entry.get("data", {}), sport)
                contracts.extend(parsed)
            except Exception as exc:
                logger.warning(
                    f"[pinnacle_live] Parse error for {sport} "
                    f"(url={entry.get('url', '?')}): {exc}"
                )

        logger.info(f"[pinnacle_live] {sport}: {len(contracts)} contracts extracted")
        return contracts

    # ── JSON parsing ───────────────────────────────────────────────────────────

    def _parse_matchups(self, data: dict | list, sport: str) -> list[dict]:
        """
        Parse an Arcadia /matchups JSON response into MarketContract-schema dicts.

        Arcadia response shape (flat list OR dict with 'matchups' key):
          [
            { "id": 1234567,
              "startTime": "2025-03-01T23:05:00Z",
              "participants": [
                { "alignment": "home", "name": "Boston Celtics", "shortName": "BOS" },
                { "alignment": "away", "name": "Dallas Mavericks", "shortName": "DAL" },
              ],
              "prices": [
                { "designation": "home", "price": -210 },
                { "designation": "away", "price": 175  },
              ]
            }, ...
          ]

        Prices with designation "draw" are included for soccer sports.
        """
        is_soccer = sport in {
            "mls", "soccer", "epl", "ligue1", "bundesliga", "seriea", "laliga",
        }

        # Arcadia may return a list directly or a dict with a "matchups" key
        if isinstance(data, list):
            matchups = data
        elif isinstance(data, dict):
            # Try common wrapper keys
            matchups = (
                data.get("matchups")
                or data.get("games")
                or data.get("competitions")
                or []
            )
        else:
            return []

        contracts: list[dict] = []
        now_iso = datetime.now(timezone.utc).isoformat()

        for m in matchups:
            try:
                contracts.extend(
                    self._parse_single_matchup(m, sport, is_soccer, now_iso)
                )
            except Exception as exc:
                logger.debug(f"[pinnacle_live] Skipping matchup {m.get('id', '?')}: {exc}")

        return contracts

    def _parse_single_matchup(
        self,
        m: dict,
        sport: str,
        is_soccer: bool,
        now_iso: str,
    ) -> list[dict]:
        """Parse one matchup entry into 2–3 MarketContract-schema dicts."""
        parts = m.get("participants", [])
        if len(parts) < 2:
            return []

        home_p = next((p for p in parts if p.get("alignment") == "home"), parts[0])
        away_p = next((p for p in parts if p.get("alignment") == "away"), parts[1])

        home_name = home_p.get("name", "")
        away_name = away_p.get("name", "")
        # Prefer shortName (e.g. "BOS") → fallback to auto-abbreviation
        home_abbr = (home_p.get("shortName") or _abbr(home_name)).upper()
        away_abbr = (away_p.get("shortName") or _abbr(away_name)).upper()

        game_title      = f"{away_name} @ {home_name}"
        game_id         = str(m.get("id", ""))
        start_time      = m.get("startTime")

        # parent_event_id must match the format produced by the Action Network
        # scanners so Kalshi ↔ Pinnacle-live keys are identical:
        #   normalize_event_key("nba BOS DAL") → "nba bos dal"
        parent_event_id = normalize_event_key(f"{sport} {home_abbr} {away_abbr}")

        # ── Prices ─────────────────────────────────────────────────────────────
        prices = m.get("prices", [])

        # Filter to moneyline prices only.
        # Pinnacle's "points" field is 0 for moneylines; non-zero = spreads/totals.
        # Some responses include a "type" field: 0 = moneyline, 1 = spread, 2 = total.
        def is_moneyline(p: dict) -> bool:
            t = p.get("type")
            pts = p.get("points", 0)
            if t is not None:
                return t == 0
            return pts == 0  # fallback: zero handicap ≈ moneyline

        ml_prices = [p for p in prices if is_moneyline(p)]

        home_price_entry = next(
            (p for p in ml_prices if p.get("designation") == "home"), None
        )
        away_price_entry = next(
            (p for p in ml_prices if p.get("designation") == "away"), None
        )

        if not home_price_entry or not away_price_entry:
            return []  # no moneyline available

        ml_home = home_price_entry.get("price")
        ml_away = away_price_entry.get("price")
        if ml_home is None or ml_away is None:
            return []

        dec_home = _american_to_decimal(ml_home)
        dec_away = _american_to_decimal(ml_away)

        contracts: list[dict] = []
        n_outcomes = 3 if is_soccer else 2

        # Home win contract
        contracts.append(_make_contract(
            platform="pinnacle_live",
            market_id=f"{game_id}_home",
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

        # Away win contract
        contracts.append(_make_contract(
            platform="pinnacle_live",
            market_id=f"{game_id}_away",
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

        # Draw contract (soccer only)
        if is_soccer:
            draw_price_entry = next(
                (p for p in ml_prices if p.get("designation") == "draw"), None
            )
            if draw_price_entry and draw_price_entry.get("price") is not None:
                ml_draw = draw_price_entry["price"]
                dec_draw = _american_to_decimal(ml_draw)
                contracts.append(_make_contract(
                    platform="pinnacle_live",
                    market_id=f"{game_id}_draw",
                    parent_event_id=parent_event_id,
                    game_title=game_title,
                    abbr="TIE",
                    team_name="Draw",
                    is_yes=True,
                    decimal_odds=dec_draw,
                    american_odds=ml_draw,
                    sport=sport,
                    start_time=start_time,
                    now_iso=now_iso,
                    n_outcomes=3,
                ))

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
                logger.error(f"[pinnacle_live] {sport} scrape failed: {exc}")

        logger.info(
            f"[pinnacle_live] Total: {len(all_contracts)} contracts "
            f"from {len(SPORT_PAGES)} sport pages."
        )
        return all_contracts

    # ── Persistence ───────────────────────────────────────────────────────────

    def save(self, contracts: list[dict]) -> Path:
        """
        Write contracts to OUTPUT_PATH as JSON.
        Creates the parent data/ directory if it doesn't exist.
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
        logger.info(f"[pinnacle_live] Saved {len(contracts)} contracts → {OUTPUT_PATH}")
        return OUTPUT_PATH

    # ── Cleanup ───────────────────────────────────────────────────────────────

    def close(self) -> None:
        """Quit the browser.  Safe to call multiple times."""
        try:
            self.driver.quit()
            logger.info("[pinnacle_live] Browser closed.")
        except Exception:
            pass


# ── Module-level helpers ───────────────────────────────────────────────────────

def _american_to_decimal(american: int | float) -> float:
    """Convert American moneyline odds to decimal (stake-inclusive) odds."""
    a = float(american)
    if a > 0:
        return (a / 100.0) + 1.0
    return (100.0 / abs(a)) + 1.0


def _init_driver(options: uc.ChromeOptions) -> uc.Chrome:
    """
    Create an undetected-chromedriver Chrome instance with the correct ChromeDriver.
    Uses webdriver-manager to download/cache the binary, bypassing uc's own
    fetch_release_number() network call which can fail on rapid re-invocations.
    See bookmaker_live._init_driver for full explanation.
    """
    from webdriver_manager.chrome import ChromeDriverManager

    chrome_major = _detect_chrome_major()
    driver_kwargs: dict = {"options": options}

    try:
        if chrome_major:
            logger.info(f"[pinnacle_live] Chrome major: {chrome_major} — fetching ChromeDriver via webdriver-manager...")
            drv_path = ChromeDriverManager(driver_version=f"LATEST_RELEASE_{chrome_major}").install()
        else:
            logger.info("[pinnacle_live] Chrome version unknown — using webdriver-manager auto-detect...")
            drv_path = ChromeDriverManager().install()

        driver_kwargs["driver_executable_path"] = drv_path
        if chrome_major:
            driver_kwargs["version_main"] = chrome_major
        logger.debug(f"[pinnacle_live] ChromeDriver path: {drv_path}")
    except Exception as exc:
        logger.warning(
            f"[pinnacle_live] webdriver-manager failed ({exc}); "
            f"falling back to uc auto-download."
        )
        if chrome_major:
            driver_kwargs["version_main"] = chrome_major

    return uc.Chrome(**driver_kwargs)


def _detect_chrome_major() -> int | None:
    """
    Return the installed Chrome major version (e.g. 145).
    Pass as version_main to uc.Chrome() to avoid ChromeDriver version mismatches.

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
                major = int(out.split()[-1].split(".")[0])
                return major
            except Exception:
                continue
    return None


def _abbr(name: str) -> str:
    """
    Fallback abbreviation when shortName is absent.
    Takes the first 4 uppercase letters of the last word in the name.
    E.g. "Boston Celtics" → "CELT", "Los Angeles Lakers" → "LAKE".
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
    american_odds: int | float,
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
    are ignored by MarketContract's strict Pydantic model but kept here
    for human inspection of the JSON file.
    """
    return {
        # ── Core MarketContract fields ────────────────────────────────────────
        "platform":             platform,
        "market_id":            market_id,
        "parent_event_id":      parent_event_id,
        "parent_event_title":   game_title,
        "outcome_label":        abbr,
        "is_yes_side":          is_yes,
        "event_title":          f"{game_title} — {abbr} to win [Pinnacle]",
        "side":                 "yes" if is_yes else "no",
        "price":                round(1.0 / decimal_odds, 6),
        "payout_per_contract":  1.0,
        "decimal_odds":         round(decimal_odds, 6),
        "num_outcomes":         n_outcomes,
        # ── Extra metadata (not in MarketContract, useful for debugging) ───────
        "american_odds":        american_odds,
        "sport":                sport,
        "start_time":           start_time,
        "scraped_at":           now_iso,
    }
