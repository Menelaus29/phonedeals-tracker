"""
crawlers/chotot.py
──────────────────
Scrapes phone listings from Chotot.com (cho tot - Vietnamese classifieds).

Chotot Search API endpoint (JSON, no JS rendering needed):
  https://gateway.chotot.com/v1/public/ad-listing?
      cg=2010&          # category: Điện thoại (phones)
      o=0&              # offset (pagination)
      page=1&
      page_size=20&
      region_v2=<id>&   # region code
      q=<query>

Conditions:
  - Chotot listings include a "type" field: "s" = sale (new/used unspecified)
    The "condition" field (params) maps to:
      1 = Mới (new), 2 = Đã dùng (used)
"""

from __future__ import annotations

import logging
import time
from typing import Generator
from urllib.parse import urlencode, quote_plus

from .base_crawler import BaseCrawler, Listing

log = logging.getLogger(__name__)

# Chotot region IDs for supported cities
CHOTOT_REGIONS: dict[str, str] = {
    "Ho Chi Minh": "13",
    "Hanoi": "12",
    "Da Nang": "34",
    "Hai Phong": "6",
    "Can Tho": "53",
}

CHOTOT_BASE = "https://gateway.chotot.com/v1/public/ad-listing"
CHOTOT_AD_BASE = "https://www.chotot.com"

MAX_PAGES = 3   # Crawl up to 3 pages per search query (20 results/page)
PAGE_SIZE = 20


class ChototCrawler(BaseCrawler):
    SOURCE = "chotot"

    def crawl(self) -> Generator[Listing, None, None]:
        region_id = CHOTOT_REGIONS.get(self.location, "")

        for item in self.watchlist.get("items", []):
            model = item["model"]
            query = self.search_query(model)
            log.info("[Chotot] Searching: '%s' in %s", query, self.location or "all regions")

            for page in range(1, MAX_PAGES + 1):
                params: dict = {
                    "cg": "2010",        # phones category
                    "o": (page - 1) * PAGE_SIZE,
                    "page": page,
                    "page_size": PAGE_SIZE,
                    "q": query,
                }
                if region_id:
                    params["region_v2"] = region_id

                api_url = f"{CHOTOT_BASE}?{urlencode(params)}"

                try:
                    resp = self.page.request.get(
                        api_url,
                        headers={
                            "Accept": "application/json",
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
                    log.error("[Chotot] API request failed for '%s' page %d: %s", query, page, exc)
                    break

                ads = data.get("ads", [])
                if not ads:
                    log.debug("[Chotot] No more results for '%s' at page %d", query, page)
                    break

                for ad in ads:
                    listing = self._parse_ad(ad)
                    if listing:
                        yield listing

                time.sleep(1.5)  # polite delay between pages

    def _parse_ad(self, ad: dict) -> Listing | None:
        try:
            list_id = ad.get("list_id") or ad.get("ad_id")
            title = ad.get("subject", "").strip()
            price = int(ad.get("price", 0) or 0)
            region = ad.get("area_name", "") or ad.get("region_name", "")

            if not title or not list_id or price == 0:
                return None

            from config import MAX_AGE_DAYS
            list_time = float(ad.get("list_time", 0))
            if list_time > 0:
                current_time_ms = time.time() * 1000
                if (current_time_ms - list_time) > (MAX_AGE_DAYS * 86400 * 1000):
                    return None

            # Condition: Chotot uses "params" list with condition_name
            condition = "unknown"
            for param in ad.get("params", []):
                if param.get("id") == "condition":
                    val = str(param.get("value", ""))
                    if val == "1":
                        condition = "new"
                    elif val == "2":
                        condition = "used"
                    break

            url = f"{CHOTOT_AD_BASE}/{list_id}.htm"

            return Listing(
                url=url,
                source="chotot",
                title=title,
                price=price,
                condition=condition,
                location=region,
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("[Chotot] Could not parse ad: %s — %s", ad, exc)
            return None
