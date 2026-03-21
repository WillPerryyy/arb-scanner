"""
Selenium + undetected-chromedriver smoke test.

Run this FIRST before the real scraper to confirm your Chrome setup works:

    cd C:\Users\willi\Claude\arb-scanner\backend
    .venv\Scripts\activate
    python test_selenium_setup.py

Expected output:
    Page title: NBA Basketball Betting Odds | Pinnacle
    URL: https://www.pinnacle.com/en/basketball/nba/matchups/
    SUCCESS — Selenium + undetected-chromedriver works

If you see a Cloudflare challenge, wait up to 30s — undetected-chromedriver
usually passes it automatically.  If it consistently fails, try running
without --headless (remove the comment below).
"""
import sys
import time

try:
    import undetected_chromedriver as uc
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.common.by import By
except ImportError as e:
    print(f"ERROR: Missing dependency — {e}")
    print("Run:  pip install selenium undetected-chromedriver webdriver-manager")
    sys.exit(1)

print("Starting Chrome via undetected-chromedriver...")

options = uc.ChromeOptions()
options.add_argument("--window-size=1400,900")
options.add_argument("--no-sandbox")
options.add_argument("--disable-dev-shm-usage")
# ← Comment out the next line to see the browser window (useful for debugging)
# options.add_argument("--headless=new")

driver = uc.Chrome(options=options)

try:
    test_url = "https://www.pinnacle.com/en/basketball/nba/"
    print(f"Navigating to {test_url} ...")
    driver.get(test_url)

    # Give Cloudflare / JS time to resolve
    print("Waiting for page to load (up to 20s)...")
    wait = WebDriverWait(driver, 20)
    wait.until(lambda d: "pinnacle" in d.title.lower() or len(d.title) > 5)

    print(f"Page title: {driver.title}")
    print(f"URL:        {driver.current_url}")

    # Quick check — does the page body contain any text at all?
    body_text = driver.find_element(By.TAG_NAME, "body").text[:200]
    if "access denied" in body_text.lower() or "403" in body_text:
        print("WARNING: May have hit a bot-detection wall. Try without headless.")
    else:
        print("Body snippet:", body_text[:100].replace("\n", " "))

    # Try to find any XHR traffic to the Arcadia API
    print("\nChecking for Arcadia API calls...")
    time.sleep(4)
    xhr_check = driver.execute_script("""
        // inject a simple flag to see if fetch has been called
        return typeof window.fetch !== 'undefined' ? 'fetch API available' : 'fetch NOT available';
    """)
    print("JS fetch status:", xhr_check)

    print("\nSUCCESS — Selenium + undetected-chromedriver is working correctly.")
    print("You can now run: python run_pinnacle_scraper.py --visible")

except Exception as exc:
    print(f"\nFAILED: {exc}")
    print("\nTroubleshooting:")
    print("  1. Is Chrome installed? Run: where chrome")
    print("  2. Is your venv activated? Run: .venv\\Scripts\\activate")
    print("  3. Try without headless by uncommenting the --headless line above")
    sys.exit(1)

finally:
    driver.quit()
    print("Browser closed.")
