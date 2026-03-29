"""
crawlers/shopee.py
──────────────────
Scrapes phone listings from Shopee Vietnam (shopee.vn).

Shopee has a public search API. We call it directly from Playwright's
request context (avoids DOM parsing, gets clean JSON).

Endpoint:
  https://shopee.vn/api/v4/search/search_items?
      by=relevancy&
      keyword=<query>&
      limit=30&
      newest=0&         # offset
      order=desc&
      page_type=search&
      scenario=PAGE_GLOBAL_SEARCH&
      version=2

Note: Shopee items don't have a "new/used" condition field — all listings
      on the platform are assumed "new" unless the seller says otherwise in
      the title. We parse "used / cũ / đã dùng" from the title.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Generator
from urllib.parse import quote_plus

from .base_crawler import BaseCrawler, Listing

log = logging.getLogger(__name__)

SHOPEE_API = "https://shopee.vn/api/v4/search/search_items"
SHOPEE_ITEM_BASE = "https://shopee.vn/product"
PAGE_SIZE = 30
MAX_PAGES = 3

# Patterns that suggest a listing is secondhand
_USED_PATTERNS = re.compile(
    r"\b(c[uũ]|second\s*hand|secondhand|đ[aã]\s*d[uù]ng|used|like\s*new|99%|98%|qua\s*s[dđ])\b",
    re.IGNORECASE,
)


class ShopeeCrawler(BaseCrawler):
    SOURCE = "shopee"

    def crawl(self) -> Generator[Listing, None, None]:
        for item in self.watchlist.get("items", []):
            model = item["model"]
            query = self.search_query(model)
            log.info("[Shopee] Searching: '%s'", query)

            for page in range(MAX_PAGES):
                newest = page * PAGE_SIZE
                api_url = (
                    f"{SHOPEE_API}"
                    f"?by=relevancy&keyword={quote_plus(query)}"
                    f"&limit={PAGE_SIZE}&newest={newest}&order=desc"
                    f"&page_type=search&scenario=PAGE_GLOBAL_SEARCH&version=2"
                )

                try:
                    resp = self.page.request.get(
                        api_url,
                        headers={
                            "Accept": "application/json",
                            "Referer": f"https://shopee.vn/search?keyword={quote_plus(query)}",
                            "User-Agent": (
                                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                "AppleWebKit/537.36 (KHTML, like Gecko) "
                                "Chrome/124.0.0.0 Safari/537.36"
                            ),
                            "X-API-SOURCE": "pc",
                        },
                        timeout=20_000,
                    )
                    data = resp.json()
                except Exception as exc:  # noqa: BLE001
                    log.error("[Shopee] API request failed for '%s' page %d: %s", query, page, exc)
                    break

                items = (
                    data.get("data", {}) or {}
                ).get("items", [])

                if not items:
                    log.debug("[Shopee] No more results for '%s' at page %d", query, page)
                    break

                for raw in items:
                    listing = self._parse_item(raw)
                    if listing:
                        yield listing

                time.sleep(1.5)

    def _parse_item(self, raw: dict) -> Listing | None:
        try:
            item_data = raw.get("item_basic", raw)

            name = str(item_data.get("name", "") or "").strip()
            price_raw = item_data.get("price", 0)  # Shopee prices are in cents (×100000)
            item_id = item_data.get("itemid")
            shop_id = item_data.get("shopid")

            if not name or not item_id or not shop_id:
                return None

            price = int(price_raw) // 100000  # convert to VND

            if price == 0:
                return None

            url = f"https://shopee.vn/product/{shop_id}/{item_id}"

            # Shopee doesn't expose condition — infer from title
            condition = "used" if _USED_PATTERNS.search(name) else "new"

            return Listing(
                url=url,
                source="shopee",
                title=name,
                price=price,
                condition=condition,
                location=self.location,
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("[Shopee] Could not parse item: %s", exc)
            return None
