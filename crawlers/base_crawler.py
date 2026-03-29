"""
crawlers/base_crawler.py
────────────────────────
Abstract base class for all crawlers.

Each crawler receives:
  - watchlist: parsed dict from watchlist.yaml  (from config.load_watchlist)
  - playwright: a running Playwright sync_api instance

Crawlers yield Listing dicts. The run_crawl() helper in this module:
  1. Calls crawler.crawl()
  2. Runs each listing against the matching engine
  3. Persists new listings to SQLite
  4. Fires Telegram alerts for deals
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Generator

log = logging.getLogger(__name__)


@dataclass
class Listing:
    """Normalised representation of a phone listing."""
    url: str
    source: str          # chotot | shopee | tiki | lazada | facebook
    title: str
    price: int           # VND
    condition: str       # new | used | unknown
    location: str = ""


class BaseCrawler(ABC):
    """
    Subclasses must implement crawl().
    They receive the watchlist and a Playwright page to do their work.
    """

    SOURCE: str = "unknown"  # override in subclass

    def __init__(self, page, watchlist: dict):
        self.page = page
        self.watchlist = watchlist
        self.location: str = watchlist.get("location", "")

    @abstractmethod
    def crawl(self) -> Generator[Listing, None, None]:
        """
        Yield Listing objects found during the crawl session.
        Implementations should handle pagination internally.
        """
        ...

    # ── Helpers available to all subclasses ───────────────────────────────────

    def search_query(self, model: str) -> str:
        """Return model name cleaned up for URL embedding."""
        import re
        return re.sub(r"\s+", " ", model).strip()

    def safe_goto(self, url: str, wait_until: str = "domcontentloaded", retries: int = 2) -> bool:
        """
        Navigate to a URL with retry logic.
        Returns True if navigation succeeded, False otherwise.
        """
        from playwright.sync_api import TimeoutError as PWTimeout

        for attempt in range(1, retries + 1):
            try:
                self.page.goto(url, wait_until=wait_until, timeout=30_000)
                return True
            except PWTimeout:
                log.warning("%s navigation timeout (attempt %d/%d): %s", self.SOURCE, attempt, retries, url)
            except Exception as exc:  # noqa: BLE001
                log.warning("%s navigation error (attempt %d/%d): %s — %s", self.SOURCE, attempt, retries, url, exc)
        return False
