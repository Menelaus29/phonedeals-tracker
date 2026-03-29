"""
crawlers/lazada.py
──────────────────
Scrapes phone listings from Lazada Vietnam (lazada.vn).

Lazada has a search page at:
  https://www.lazada.vn/catalog/?q=<query>&c=5000356  (c = phones category)

We use the embedded JSON data (_data_ store) Lazada injects into the HTML,
then fall back to DOM scraping if the JSON isn't found.

All Lazada listings are officially new items. Condition = "new".
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Generator
from urllib.parse import quote_plus

from playwright.sync_api import TimeoutError as PWTimeout

from .base_crawler import BaseCrawler, Listing

log = logging.getLogger(__name__)

LAZADA_BASE = "https://www.lazada.vn/catalog/"
LAZADA_CATEGORY = "5000356"  # Phones & Tablets
MAX_PAGES = 3
PAGE_SIZE = 40


class LazadaCrawler(BaseCrawler):
    SOURCE = "lazada"

    def crawl(self) -> Generator[Listing, None, None]:
        for item in self.watchlist.get("items", []):
            if item.get("condition") == "used":
                log.debug("[Lazada] Skipping '%s' (condition=used, Lazada is new-only).", item["model"])
                continue

            model = item["model"]
            query = self.search_query(model)
            log.info("[Lazada] Searching: '%s'", query)

            for page in range(1, MAX_PAGES + 1):
                url = (
                    f"{LAZADA_BASE}"
                    f"?q={quote_plus(query)}"
                    f"&c={LAZADA_CATEGORY}"
                    f"&page={page}"
                )
                if not self.safe_goto(url, wait_until="networkidle"):
                    break

                # --- Try extracting embedded JSON ---
                listings = self._extract_from_json() or self._extract_from_dom()

                for listing in listings:
                    yield listing

                # Check for next page
                try:
                    next_btn = self.page.query_selector("li.ant-pagination-next:not(.ant-pagination-disabled)")
                    if not next_btn:
                        break
                except Exception:
                    break

                time.sleep(2)

    # ── JSON extraction ────────────────────────────────────────────────────────

    def _extract_from_json(self) -> list[Listing]:
        """
        Lazada injects a window.__globalStoreDataLayer or similar JSON blob.
        We try to extract it from the page's script tags.
        """
        try:
            content = self.page.content()
            # Look for the main data JSON blob Lazada embeds in script tags
            match = re.search(
                r'window\.__moduleData__\s*=\s*(\{.*?"listItems".*?\})\s*;',
                content,
                re.DOTALL,
            )
            if not match:
                return []

            # This can be megabytes — use a targeted regex to pull item arrays
            items_match = re.search(r'"listItems"\s*:\s*(\[.*?\])\s*,\s*"', content, re.DOTALL)
            if not items_match:
                return []

            items_json = items_match.group(1)
            items = json.loads(items_json)
            return [l for l in (self._parse_json_item(i) for i in items) if l]
        except Exception as exc:  # noqa: BLE001
            log.debug("[Lazada] JSON extraction failed: %s", exc)
            return []

    def _parse_json_item(self, item: dict) -> Listing | None:
        try:
            name = str(item.get("name", "") or item.get("productName", "")).strip()
            price_str = str(item.get("price", "0")).replace(",", "").replace(".", "")
            price = int(re.sub(r"[^\d]", "", price_str) or "0")
            item_url = item.get("detailUrl", "")

            if not name or not item_url or price == 0:
                return None

            if not item_url.startswith("http"):
                item_url = "https:" + item_url

            return Listing(
                url=item_url,
                source="lazada",
                title=name,
                price=price,
                condition="new",
                location=self.location,
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("[Lazada] Could not parse JSON item: %s", exc)
            return None

    # ── DOM fallback ───────────────────────────────────────────────────────────

    def _extract_from_dom(self) -> list[Listing]:
        """
        DOM-based fallback. Lazada product cards use data-spm attributes.
        """
        results = []
        try:
            cards = self.page.query_selector_all("[data-tracking='product-card']")
            if not cards:
                # Try alternate selector
                cards = self.page.query_selector_all(".Bm3ON")

            for card in cards:
                listing = self._parse_dom_card(card)
                if listing:
                    results.append(listing)
        except Exception as exc:  # noqa: BLE001
            log.debug("[Lazada] DOM extraction failed: %s", exc)
        return results

    def _parse_dom_card(self, card) -> Listing | None:
        try:
            title_el = card.query_selector("[class*='title']") or card.query_selector("a")
            price_el = card.query_selector("[class*='price']")
            link_el = card.query_selector("a[href]")

            if not title_el or not price_el or not link_el:
                return None

            title = title_el.inner_text().strip()
            price_text = re.sub(r"[^\d]", "", price_el.inner_text())
            price = int(price_text) if price_text else 0
            url = link_el.get_attribute("href") or ""

            if not url.startswith("http"):
                url = "https:" + url

            if not title or price == 0 or not url:
                return None

            return Listing(
                url=url,
                source="lazada",
                title=title,
                price=price,
                condition="new",
                location=self.location,
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("[Lazada] DOM card parse error: %s", exc)
            return None
