"""
crawlers/tiki.py
────────────────
Scrapes phone listings from Tiki.vn.

Tiki has a public search API:
  https://tiki.vn/api/v2/products?
      q=<query>&
      category=1795&   # Điện thoại (phones)
      page=1&
      limit=40&
      sort=default

All Tiki listings are officially new items sold by shops. Condition = "new".
"""

from __future__ import annotations

import logging
import time
from typing import Generator
from urllib.parse import quote_plus

from .base_crawler import BaseCrawler, Listing

log = logging.getLogger(__name__)

TIKI_API = "https://tiki.vn/api/v2/products"
TIKI_ITEM_BASE = "https://tiki.vn"
PAGE_SIZE = 40
MAX_PAGES = 3


class TikiCrawler(BaseCrawler):
    SOURCE = "tiki"

    def crawl(self) -> Generator[Listing, None, None]:
        for item in self.watchlist.get("items", []):
            # Tiki is a retailer — only makes sense for new phones
            if item.get("condition") == "used":
                log.debug("[Tiki] Skipping '%s' (condition=used, Tiki is new-only).", item["model"])
                continue

            model = item["model"]
            query = self.search_query(model)
            log.info("[Tiki] Searching: '%s'", query)

            for page in range(1, MAX_PAGES + 1):
                api_url = (
                    f"{TIKI_API}"
                    f"?q={quote_plus(query)}"
                    f"&category=1795"
                    f"&page={page}&limit={PAGE_SIZE}&sort=default"
                )

                try:
                    resp = self.page.request.get(
                        api_url,
                        headers={
                            "Accept": "application/json",
                            "Referer": f"https://tiki.vn/search?q={quote_plus(query)}",
                            "User-Agent": (
                                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                "AppleWebKit/537.36 (KHTML, like Gecko) "
                                "Chrome/124.0.0.0 Safari/537.36"
                            ),
                        },
                        timeout=20_000,
                    )
                    data = resp.json()
                except Exception as exc:  # noqa: BLE001
                    log.error("[Tiki] API request failed for '%s' page %d: %s", query, page, exc)
                    break

                products = data.get("data", [])
                if not products:
                    log.debug("[Tiki] No more results for '%s' at page %d", query, page)
                    break

                for product in products:
                    listing = self._parse_product(product)
                    if listing:
                        yield listing

                time.sleep(1.5)

    def _parse_product(self, product: dict) -> Listing | None:
        try:
            name = str(product.get("name", "") or "").strip()
            price = int(product.get("price", 0) or 0)
            product_id = product.get("id")
            url_key = product.get("url_key", "")

            if not name or not product_id or price == 0:
                return None

            url = f"{TIKI_ITEM_BASE}/{url_key}.html?spid={product_id}"

            return Listing(
                url=url,
                source="tiki",
                title=name,
                price=price,
                condition="new",     # Tiki = always new
                location=self.location,
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("[Tiki] Could not parse product: %s", exc)
            return None
