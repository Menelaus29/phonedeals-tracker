"""
main.py
───────
Entry point for the Phone Deal Tracker.

Scheduler:
  - Standard job (Chotot, Shopee, Tiki, Lazada): runs every 30 minutes.
  - Facebook job (Marketplace + Groups): runs every 60 minutes.

Browser session:
  - Standard crawlers share one Playwright context (can be headless).
  - Facebook uses its OWN persistent context stored in BROWSER_DATA_DIR.
    On first run, a visible window opens so you can log in manually.

How to run:
    python main.py
"""

from __future__ import annotations

import logging
import signal
import sys
import time
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.events import EVENT_JOB_ERROR
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

import config
import db
import notifier
from crawlers import ChototCrawler, ShopeeCrawler, TikiCrawler, LazadaCrawler, FacebookCrawler
from crawlers.base_crawler import Listing
from matcher import matches_watchlist_item

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("tracker.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("main")


# ── Core processing pipeline ──────────────────────────────────────────────────

def process_listing(listing: Listing, watchlist: dict) -> None:
    """
    Given a crawled listing:
    1. Deduplicate against DB.
    2. Run against every watchlist item.
    3. If a match is found, save to DB and fire Telegram alert.
    """
    if db.is_seen(listing.url):
        return

    for item in watchlist.get("items", []):
        is_match, pct_below = matches_watchlist_item(
            listing_title=listing.title,
            listing_condition=listing.condition,
            listing_price=listing.price,
            watchlist_item=item,
            threshold=item["threshold"],
        )

        if is_match:
            log.info(
                "✅ DEAL: [%s] '%s' @ %s₫ (%.1f%% below threshold for %s)",
                listing.source, listing.title, f"{listing.price:,}", pct_below, item["model"],
            )

            alert_sent = notifier.send_deal_alert(
                title=listing.title,
                price=listing.price,
                pct_below=pct_below,
                condition=listing.condition,
                source=listing.source,
                location=listing.location or watchlist.get("location", ""),
                url=listing.url,
                matched_model=item["model"],
                bot_token=config.TELEGRAM_BOT_TOKEN,
                chat_id=config.TELEGRAM_CHAT_ID,
            )

            db.save_listing(
                url=listing.url,
                source=listing.source,
                title=listing.title,
                price=listing.price,
                condition=listing.condition,
                location=listing.location or watchlist.get("location", ""),
                matched_model=item["model"],
                pct_below=pct_below,
                alerted=alert_sent,
            )
            # Only alert for the FIRST matching watchlist item per listing
            break
    else:
        # Not a deal — still save to DB to prevent re-crawling
        db.save_listing(
            url=listing.url,
            source=listing.source,
            title=listing.title,
            price=listing.price,
            condition=listing.condition,
            location=listing.location,
            matched_model=None,
            pct_below=None,
            alerted=False,
        )


# ── Standard crawl job (Chotot, Shopee, Tiki, Lazada) ────────────────────────

def run_standard_crawl() -> None:
    watchlist = config.load_watchlist()
    log.info("=== Standard crawl started ===")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=config.HEADLESS)
        ctx = browser.new_context(
            locale="vi-VN",
            timezone_id="Asia/Ho_Chi_Minh",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page = ctx.new_page()
        Stealth().apply_stealth_sync(page)

        crawler_classes = [ChototCrawler, ShopeeCrawler, TikiCrawler, LazadaCrawler]
        for CrawlerClass in crawler_classes:
            crawler = CrawlerClass(page=page, watchlist=watchlist)
            name = CrawlerClass.SOURCE
            try:
                count = 0
                for listing in crawler.crawl():
                    process_listing(listing, watchlist)
                    count += 1
                log.info("[%s] Crawl finished. %d listings processed.", name, count)
            except Exception as exc:  # noqa: BLE001
                log.error("[%s] Crawl error: %s", name, exc, exc_info=True)

        ctx.close()
        browser.close()

    log.info("=== Standard crawl complete ===")


# ── Facebook crawl job ────────────────────────────────────────────────────────

def run_facebook_crawl() -> None:
    watchlist = config.load_watchlist()
    log.info("=== Facebook crawl started ===")

    browser_data = Path(config.BROWSER_DATA_DIR).resolve()

    with sync_playwright() as pw:
        first_run = not any(browser_data.iterdir()) if browser_data.exists() else True

        if first_run:
            # Open visible browser for manual login
            log.info(
                "🔐 First run: browser will open so you can log in to Facebook.\n"
                "   Please log in. The tracker will automatically continue once done."
            )
            ctx = pw.chromium.launch_persistent_context(
                str(browser_data),
                headless=False,
                locale="vi-VN",
                timezone_id="Asia/Ho_Chi_Minh",
            )
            page = ctx.new_page()
            page.goto("https://www.facebook.com/login")
            
            log.info("\n👉 Please log in to Facebook. Waiting up to 5 minutes...\n")
            try:
                # Wait for the Marketplace icon in the sidebar which proves we are logged in
                page.wait_for_selector("a[href*='/marketplace']", timeout=300000)
                log.info("✅ Login detected! Continuing...")
                page.wait_for_timeout(3000)
            except Exception as exc:
                log.warning("Login wait timed out or browser closed. Continuing... (%s)", exc)
        else:
            ctx = pw.chromium.launch_persistent_context(
                str(browser_data),
                headless=config.HEADLESS,
                locale="vi-VN",
                timezone_id="Asia/Ho_Chi_Minh",
            )
            page = ctx.new_page()
            Stealth().apply_stealth_sync(page)

        crawler = FacebookCrawler(page=page, watchlist=watchlist)
        try:
            count = 0
            for listing in crawler.crawl():
                process_listing(listing, watchlist)
                count += 1
            log.info("[Facebook] Crawl finished. %d listings processed.", count)
        except Exception as exc:  # noqa: BLE001
            log.error("[Facebook] Crawl error: %s", exc, exc_info=True)
        finally:
            ctx.close()

    log.info("=== Facebook crawl complete ===")


# ── APScheduler setup ─────────────────────────────────────────────────────────

def on_job_error(event) -> None:
    log.error("Scheduler job crashed: %s", event.exception)


def main() -> None:
    # Validate config early
    config.validate_config()

    # Boot database
    db.init_db()

    # Purge old non-deals
    db.purge_old_non_deals(days=7)

    # ── Retry any deals whose Telegram alert previously failed ──────────────
    unsent = db.get_unsent_deals()
    if unsent:
        log.info("Retrying %d unsent deal alert(s) from previous runs...", len(unsent))
        for row in unsent:
            sent = notifier.send_deal_alert(
                title=row["title"],
                price=row["price"],
                pct_below=row["pct_below"],
                condition=row["condition"],
                source=row["source"],
                location=row["location"] or "",
                url=row["url"],
                matched_model=row["matched_model"],
                bot_token=config.TELEGRAM_BOT_TOKEN,
                chat_id=config.TELEGRAM_CHAT_ID,
            )
            if sent:
                db.mark_alerted(row["url"])

    # Send startup ping
    notifier.send_startup_ping(
        bot_token=config.TELEGRAM_BOT_TOKEN,
        chat_id=config.TELEGRAM_CHAT_ID,
    )

    # Run crawls once on startup for immediate results
    log.info("Running initial crawls on startup...")
    try:
        run_standard_crawl()
    except Exception as exc:  # noqa: BLE001
        log.error("Initial standard crawl failed: %s", exc, exc_info=True)

    try:
        run_facebook_crawl()
    except Exception as exc:  # noqa: BLE001
        log.error("Initial Facebook crawl failed: %s", exc, exc_info=True)

    # Set up scheduler
    scheduler = BackgroundScheduler(timezone="Asia/Ho_Chi_Minh")
    scheduler.add_listener(on_job_error, EVENT_JOB_ERROR)

    scheduler.add_job(
        run_standard_crawl,
        trigger="interval",
        minutes=30,
        id="standard_crawl",
        name="Chotot / Shopee / Tiki / Lazada",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        run_facebook_crawl,
        trigger="interval",
        minutes=60,
        id="fb_crawl",
        name="Facebook Marketplace & Groups",
        max_instances=1,
        coalesce=True,
    )

    scheduler.start()
    log.info(
        "Scheduler running. Standard crawl every 30 min, Facebook every 60 min. "
        "Press Ctrl+C to stop."
    )

    # Keep the main thread alive; handle graceful shutdown
    def _shutdown(sig, frame):
        log.info("Shutting down scheduler...")
        scheduler.shutdown(wait=False)
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    while True:
        time.sleep(60)


if __name__ == "__main__":
    main()
