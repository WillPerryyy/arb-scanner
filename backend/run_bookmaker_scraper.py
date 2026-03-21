"""
Standalone CLI runner for the Bookmaker.eu live odds scraper.

Usage:
    # From the backend/ directory with venv activated:
    python run_bookmaker_scraper.py                   # headless (default)
    python run_bookmaker_scraper.py --visible          # visible window (debug)
    python run_bookmaker_scraper.py --sport nba nhl    # specific sports only
    python run_bookmaker_scraper.py --out /tmp/out.json  # custom output path

Output:
    backend/data/bookmaker_live.json  (or the path passed via --out)
    Schema:
      {
        "scraped_at": "<ISO timestamp>",
        "count": <N>,
        "contracts": [
          {
            "platform":         "bookmaker_live",
            "market_id":        "nba bos dal_home",    ← stable across runs
            "parent_event_id":  "nba bos dal",         ← matches Kalshi/DK namespace
            "outcome_label":    "BOS",
            "is_yes_side":      true,
            "price":            0.476190,              ← 1/decimal_odds
            "decimal_odds":     2.1,
            "american_odds":    110,
            "sport":            "nba",
            "start_time":       "3/01 12:42pm PT",
            ...
          },
          ...
        ]
      }

Cron example (every 5 minutes):
    */5 * * * * cd /path/to/backend && .venv/bin/python run_bookmaker_scraper.py >> logs/bookmaker.log 2>&1

Schema verification (run after first successful scrape):
    python -c "
    import json
    data = json.load(open('data/bookmaker_live.json'))
    c = data['contracts'][0]
    required = ['platform','market_id','parent_event_id','outcome_label',
                'is_yes_side','event_title','side','price','decimal_odds']
    missing = [f for f in required if f not in c]
    print('Missing fields:', missing or 'NONE — schema OK')
    print('Sample:')
    print(json.dumps(c, indent=2))
    "
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

# Ensure the backend/ package root is importable when running as a plain script
sys.path.insert(0, str(Path(__file__).parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scrape live moneyline odds from Bookmaker.eu via Selenium.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--visible",
        action="store_true",
        help="Open a visible browser window (useful for debugging).",
    )
    parser.add_argument(
        "--sport",
        nargs="+",
        metavar="SPORT",
        help=(
            "Scrape only these sport slugs (e.g. nba nhl mlb). "
            "Defaults to all sports in SPORT_PAGES."
        ),
    )
    parser.add_argument(
        "--out",
        metavar="PATH",
        help="Custom output path for the JSON file (default: backend/data/bookmaker_live.json).",
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="Run the scraper but do not write the JSON file (useful for dry runs).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    from scanners.bookmaker_live import BookmakerLiveScraper, SPORT_PAGES, OUTPUT_PATH

    # Build the filtered sport→url map if --sport was passed
    if args.sport:
        requested = {s.lower() for s in args.sport}
        filtered_pages = {
            url: sport
            for url, sport in SPORT_PAGES.items()
            if sport in requested
        }
        missing = requested - set(filtered_pages.values())
        if missing:
            logger.warning(
                f"Unknown sport slug(s): {sorted(missing)}. "
                f"Available: {sorted(set(SPORT_PAGES.values()))}"
            )
        if not filtered_pages:
            logger.error("No valid sport pages to scrape. Exiting.")
            return 1
    else:
        filtered_pages = SPORT_PAGES

    out_path = Path(args.out) if args.out else OUTPUT_PATH

    logger.info(
        f"Starting Bookmaker.eu live scraper — "
        f"{'visible' if args.visible else 'headless'} mode, "
        f"{len(filtered_pages)} sport page(s)"
    )
    t0 = time.monotonic()

    scraper = BookmakerLiveScraper(headless=not args.visible)
    try:
        # Temporarily patch module-level SPORT_PAGES if --sport filter was used
        if args.sport:
            import scanners.bookmaker_live as _mod
            _orig = _mod.SPORT_PAGES
            _mod.SPORT_PAGES = filtered_pages
            try:
                contracts = scraper.scrape_all()
            finally:
                _mod.SPORT_PAGES = _orig
        else:
            contracts = scraper.scrape_all()

        elapsed = time.monotonic() - t0
        logger.info(f"Scrape complete in {elapsed:.1f}s — {len(contracts)} contracts found.")

        if not contracts:
            logger.warning(
                "No contracts were returned. Possible causes:\n"
                "  • No games currently scheduled for these sports\n"
                "  • Page loaded a 'futures' section instead of a game board\n"
                "  • DOM IDs changed (run with --visible to inspect)\n"
                "  • Site is in maintenance mode"
            )

        if not args.no_save:
            if args.out:
                out_path.parent.mkdir(parents=True, exist_ok=True)
                with open(out_path, "w", encoding="utf-8") as f:
                    json.dump(
                        {
                            "scraped_at": datetime.now(timezone.utc).isoformat(),
                            "count": len(contracts),
                            "contracts": contracts,
                        },
                        f,
                        indent=2,
                    )
                logger.info(f"Output written → {out_path}")
            else:
                scraper.save(contracts)

        # Print summary table
        if contracts:
            by_sport = Counter(c.get("sport", "?") for c in contracts)
            print("\n── Contracts by sport ───────────────────────────")
            for sport, n in sorted(by_sport.items()):
                print(f"  {sport:<14}  {n:>4} contracts")
            print(f"  {'TOTAL':<14}  {len(contracts):>4} contracts")
            print("────────────────────────────────────────────────")
            print(f"\nOutput: {out_path}")
        else:
            print("\nNo contracts scraped.")

    finally:
        scraper.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
