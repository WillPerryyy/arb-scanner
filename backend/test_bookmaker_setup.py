"""
Smoke test for Bookmaker.eu Selenium scraper.

Run this BEFORE using the full scraper to confirm:
  1. undetected-chromedriver launches Chrome without errors
  2. lines.bookmaker.eu loads successfully (no maintenance / block page)
  3. The expected DOM element IDs (#vN_1, #hN_1, #vM_1, #hM_1) are present
  4. Moneyline values and team names look reasonable

Usage:
    cd C:\\Users\\willi\\Claude\\arb-scanner\\backend
    .venv\\Scripts\\activate
    python test_bookmaker_setup.py

    # Show the browser window (useful for visual inspection):
    python test_bookmaker_setup.py --visible
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# Force UTF-8 output on Windows (avoids CP1252 encoding errors for ✓/✗/─ chars)
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf-8-sig"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException


TEST_URL = "https://lines.bookmaker.eu/en/sports/basketball/nba"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--visible", action="store_true", help="Show browser window")
    return p.parse_args()


def run_smoke_test(headless: bool = True) -> bool:
    print("─" * 60)
    print("Bookmaker.eu Selenium smoke test")
    print(f"URL: {TEST_URL}")
    print("─" * 60)

    options = uc.ChromeOptions()
    options.add_argument("--window-size=1400,900")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    if headless:
        options.add_argument("--headless=new")

    driver = None
    passed = True
    try:
        print("\n[1/4] Launching Chrome with undetected-chromedriver...")
        # Use _init_driver() which pre-downloads ChromeDriver via webdriver-manager
        # to avoid uc's own patcher network calls (which can fail on rapid re-invocations).
        from scanners.bookmaker_live import _init_driver, _detect_chrome_major
        chrome_major = _detect_chrome_major()
        if chrome_major:
            print(f"      Detected Chrome major version: {chrome_major}")
        driver = _init_driver(options)
        print("      ✓ Chrome launched")

        print(f"\n[2/4] Navigating to {TEST_URL}...")
        driver.get(TEST_URL)
        time.sleep(4)  # server-rendered, 4s is plenty

        page_title = driver.title or ""
        print(f"      Page title: '{page_title}'")

        # Check for obvious error pages
        body_text = ""
        try:
            body_text = driver.find_element(By.TAG_NAME, "body").text[:300].lower()
        except Exception:
            pass

        suspicious = any(
            kw in body_text or kw in page_title.lower()
            for kw in ("403", "blocked", "access denied", "maintenance", "error")
        )
        if suspicious:
            print("      ⚠  Page may be blocked or in maintenance. Run with --visible to inspect.")
            passed = False
        else:
            print("      ✓ Page loaded (no obvious block/error detected)")

        print("\n[3/4] Checking for expected DOM element IDs...")
        # Try to find game 1 elements
        found_any = False
        results: list[tuple[str, str, str]] = []  # (element_id, status, value)

        for eid in (f"vN_1", f"hN_1", f"vM_1", f"hM_1"):
            try:
                el = driver.find_element(By.ID, eid)
                val = el.text.strip()
                results.append((eid, "✓", val or "(empty)"))
                found_any = True
            except NoSuchElementException:
                results.append((eid, "✗", "NOT FOUND"))

        for eid, status, val in results:
            print(f"      {status}  #{eid:<12}  →  '{val}'")

        if not found_any:
            print("\n      ⚠  No game elements found for game #1.")
            print("         Possible reasons:")
            print("         • No NBA games are currently scheduled")
            print("         • Page loaded the futures section instead of the game board")
            print("         • DOM IDs have changed (run with --visible to inspect)")
            passed = False
        else:
            # Count total games
            n = 1
            while True:
                try:
                    driver.find_element(By.ID, f"vN_{n}")
                    n += 1
                except NoSuchElementException:
                    break
            total_games = n - 1
            print(f"\n      Found {total_games} game(s) total on this page.")

        print("\n[4/4] Quick import check for bookmaker_live module...")
        try:
            from scanners.bookmaker_live import (
                BookmakerLiveScraper,
                SPORT_PAGES,
                _parse_moneyline,
                TEAM_ABBR_MAP,
            )
            # Sanity-check _parse_moneyline
            assert _parse_moneyline("+930") == 930,  "parse +930"
            assert _parse_moneyline("-145") == -145, "parse -145"
            assert _parse_moneyline("PK") == -110,   "parse PK"
            assert _parse_moneyline("OFF") is None,  "parse OFF"
            assert _parse_moneyline("") is None,     "parse empty"
            print("      ✓ bookmaker_live module imports correctly")
            print(f"      ✓ SPORT_PAGES has {len(SPORT_PAGES)} entries")
            print(f"      ✓ TEAM_ABBR_MAP has {len(TEAM_ABBR_MAP)} entries")
            print("      ✓ _parse_moneyline() passes all cases")
        except Exception as exc:
            print(f"      ✗ Import or assertion failed: {exc}")
            passed = False

    except Exception as exc:
        print(f"\n✗ Unexpected error: {exc}")
        passed = False
    finally:
        if driver:
            driver.quit()
            print("\n[done] Browser closed.")

    print("\n" + "─" * 60)
    if passed:
        print("RESULT: ✓ PASSED — ready to run run_bookmaker_scraper.py")
        print("\nNext steps:")
        print("  python run_bookmaker_scraper.py --visible   # debug run")
        print("  python run_bookmaker_scraper.py             # headless run")
    else:
        print("RESULT: ✗ SOME CHECKS FAILED — see warnings above")
        print("\nTroubleshooting:")
        print("  1. Run with --visible to watch the browser")
        print("  2. Verify lines.bookmaker.eu loads in your normal browser")
        print("  3. Check that game IDs are vN_1/hN_1 in the page source (Ctrl+U)")
    print("─" * 60)

    return passed


if __name__ == "__main__":
    args = parse_args()
    ok = run_smoke_test(headless=not args.visible)
    sys.exit(0 if ok else 1)
