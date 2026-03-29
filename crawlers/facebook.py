"""
crawlers/facebook.py
────────────────────
Scrapes Facebook Marketplace (filtered by location) and user-defined
Facebook Group URLs.

Authentication:
  - Uses a persistent Playwright browser context stored in BROWSER_DATA_DIR.
  - On the very first run (if the directory is empty/doesn't exist), the bot
    opens a visible browser window and waits for the user to log in manually.
    After login, it saves the session to disk automatically.
  - All subsequent runs reuse the saved session headlessly.

Facebook Marketplace URL pattern (Vietnam, by location):
  https://www.facebook.com/marketplace/<city_code>/search?query=<q>

Facebook Groups URL pattern:
  <group_url>/search/?q=<query>
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Generator
from urllib.parse import quote_plus

from playwright.sync_api import TimeoutError as PWTimeout, Page

from .base_crawler import BaseCrawler, Listing

log = logging.getLogger(__name__)

# Facebook Marketplace city codes (Vietnam cities)
FB_CITY_CODES: dict[str, str] = {
    "Ho Chi Minh": "ho-chi-minh-city",
    "Hanoi": "hanoi",
    "Da Nang": "da-nang",
    "Hai Phong": "hai-phong",
    "Can Tho": "can-tho",
}

FB_MARKETPLACE_BASE = "https://www.facebook.com/marketplace"
MAX_SCROLL_ROUNDS = 5   # How many times to scroll down + wait for more results
SCROLL_PAUSE = 2.5      # Seconds between scrolls


class FacebookCrawler(BaseCrawler):
    """
    NOTE: This crawler requires its OWN persistent browser context,
    managed separately in main.py. The page passed here should already
    be authenticated with Facebook.
    """
    SOURCE = "facebook"

    def crawl(self) -> Generator[Listing, None, None]:
        city_code = FB_CITY_CODES.get(self.location, "")

        for item in self.watchlist.get("items", []):
            model = item["model"]
            query = self.search_query(model)

            # ── Marketplace ───────────────────────────────────────────────────
            if city_code:
                mp_url = (
                    f"{FB_MARKETPLACE_BASE}/{city_code}/search"
                    f"?query={quote_plus(query)}&exact=false"
                )
            else:
                mp_url = (
                    f"{FB_MARKETPLACE_BASE}/search"
                    f"?query={quote_plus(query)}&exact=false"
                )

            log.info("[Facebook] Marketplace search: '%s' in %s", query, self.location or "all")
            yield from self._scrape_marketplace_or_group(mp_url, is_group=False)

            # ── Facebook Groups ───────────────────────────────────────────────
            from config import FB_GROUP_URLS

            for group_url in FB_GROUP_URLS:
                search_url = f"{group_url.rstrip('/')}/search/?q={quote_plus(query)}"
                log.info("[Facebook] Group search: '%s' at %s", query, group_url)
                yield from self._scrape_marketplace_or_group(search_url, is_group=True)

            time.sleep(3)

    def _scrape_marketplace_or_group(self, url: str, is_group: bool) -> Generator[Listing, None, None]:
        if not self.safe_goto(url, wait_until="domcontentloaded"):
            return

        # Give React a moment to render
        time.sleep(3)
        self._dismiss_login_popup()

        seen_urls: set[str] = set()

        for _ in range(MAX_SCROLL_ROUNDS):
            if is_group:
                cards = self._extract_group_posts()
            else:
                cards = self._extract_marketplace_cards()

            for listing in cards:
                if listing.url not in seen_urls:
                    seen_urls.add(listing.url)
                    yield listing

            # Scroll down
            self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(SCROLL_PAUSE)

    def _dismiss_login_popup(self) -> None:
        """Close Facebook's 'log in to continue' modal if it appears."""
        try:
            close_btn = self.page.wait_for_selector(
                "div[aria-label='Close'][role='button']",
                timeout=3_000,
            )
            if close_btn:
                close_btn.click()
                time.sleep(0.5)
        except Exception:
            pass

    # ── Marketplace card extraction ────────────────────────────────────────────

    def _extract_marketplace_cards(self) -> list[Listing]:
        results = []
        try:
            # Marketplace item cards are anchors containing price + title text
            cards = self.page.query_selector_all("a[href*='/marketplace/item/']")
            for card in cards:
                listing = self._parse_marketplace_card(card)
                if listing:
                    results.append(listing)
        except Exception as exc:  # noqa: BLE001
            log.debug("[Facebook] Marketplace card extraction error: %s", exc)
        return results

    def _parse_marketplace_card(self, card) -> Listing | None:
        try:
            href = card.get_attribute("href") or ""
            if not href:
                return None

            url = "https://www.facebook.com" + href.split("?")[0] if href.startswith("/") else href
            url = url.rstrip("/")

            # Text content of the card: usually "Price\nTitle\nLocation"
            text = card.inner_text().strip()
            lines = [l.strip() for l in text.splitlines() if l.strip()]
            if len(lines) < 2:
                return None

            price = self._parse_price(lines[0])
            if price == 0:
                # Try second line if first line wasn't a price
                price = self._parse_price(lines[1])
                title = lines[0] if price else lines[1]
            else:
                title = lines[1] if len(lines) > 1 else ""

            if not title or price == 0:
                return None

            if not self._is_recent_enough(text):
                return None

            condition = self._infer_condition(title)

            return Listing(
                url=url,
                source="facebook",
                title=title,
                price=price,
                condition=condition,
                location=self.location,
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("[Facebook] Card parse error: %s", exc)
            return None

    # ── Group post extraction ──────────────────────────────────────────────────

    def _extract_group_posts(self) -> list[Listing]:
        results = []
        try:
            # Group search results: posts with links to the post itself
            posts = self.page.query_selector_all("div[role='article']")
            for post in posts:
                listing = self._parse_group_post(post)
                if listing:
                    results.append(listing)
        except Exception as exc:  # noqa: BLE001
            log.debug("[Facebook] Group post extraction error: %s", exc)
        return results

    def _parse_group_post(self, post) -> Listing | None:
        try:
            # Get link to the post
            link_el = post.query_selector("a[href*='/groups/'][href*='/posts/']")
            if not link_el:
                return None

            href = link_el.get_attribute("href") or ""
            url = href.split("?")[0]
            if url.startswith("/"):
                url = "https://www.facebook.com" + url

            date_text = link_el.inner_text().strip()
            if date_text and getattr(link_el, "inner_text", None):
                if not self._is_recent_enough(date_text):
                    return None

            # Get post text content
            content_el = post.query_selector("div[data-ad-comet-preview='message']") or \
                         post.query_selector("div[dir='auto']")
            if not content_el:
                return None

            text = content_el.inner_text().strip()
            if not text:
                return None

            # Try to extract price from text
            price = self._parse_price_from_text(text)
            if price == 0:
                return None

            # Use first 200 chars of post as title
            title = text[:200].replace("\n", " ").strip()
            condition = self._infer_condition(text)

            return Listing(
                url=url,
                source="facebook",
                title=title,
                price=price,
                condition=condition,
                location=self.location,
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("[Facebook] Group post parse error: %s", exc)
            return None

    # ── Price parsing helpers ──────────────────────────────────────────────────

    @staticmethod
    def _parse_price(text: str) -> int:
        """Parse a price string like '18.500.000đ' or '18,500,000₫' into an int."""
        digits = re.sub(r"[^\d]", "", text)
        return int(digits) if digits and int(digits) > 100_000 else 0

    @staticmethod
    def _parse_price_from_text(text: str) -> int:
        """Search for price patterns comprehensively in free-form post text."""
        # 1. Fractional shorthand: e.g. "8tr2", "8m25", "8 củ 2", "8tr250k"
        frac_pattern = r"(\d+)\s*(?:tr|triệu|củ|m|c)\s*(\d{1,3})(?:k)?(?:\b|\s|$)"
        m = re.search(frac_pattern, text, re.IGNORECASE)
        if m:
            base = int(m.group(1)) * 1_000_000
            frac_str = m.group(2)
            fraction = int(frac_str) * (10 ** (6 - len(frac_str)))
            price = base + fraction
            if price > 100_000:
                return price

        # 2. Standard Shorthand: 18.5tr, 18 triệu
        short_pattern = r"(\d+(?:[.,]\d+)?)\s*(?:tr(?:iệu)?|m(?:il)?|củ)\b"
        m = re.search(short_pattern, text, re.IGNORECASE)
        if m:
            price_str = m.group(1).replace(",", ".")
            try:
                price = int(float(price_str) * 1_000_000)
                if price > 100_000:
                    return price
            except ValueError:
                pass

        # 3. Explicit VND amount with currency symbol: 18.500.000đ
        explicit_pattern = r"(\d{1,3}(?:[.,]\d{3}){1,4}|\d{6,})\s*(?:đ|₫|vnd|đồng)(?:\b|\s|$)"
        m = re.search(explicit_pattern, text, re.IGNORECASE)
        if m:
            price_str = re.sub(r"[^\d]", "", m.group(1))
            try:
                price = int(price_str)
                if price > 100_000:
                    return price
            except ValueError:
                pass

        # 4. Fallback explicit without currency (e.g. 18.500.000)
        fallback_pattern = r"\b(\d{1,3}(?:[.,]\d{3}){2,4})\b"
        m = re.search(fallback_pattern, text)
        if m:
            price_str = re.sub(r"[^\d]", "", m.group(1))
            try:
                price = int(price_str)
                if price > 100_000:
                    return price
            except ValueError:
                pass

        return 0

    @staticmethod
    def _is_recent_enough(date_text: str) -> bool:
        """
        Parses relative time strings like "Just now", "2 hrs", "Yesterday", "May 8", "1 w".
        Returns False if the date text indicates the post is older than MAX_AGE_DAYS.
        """
        import datetime
        from config import MAX_AGE_DAYS

        text = date_text.lower().strip()
        if not text:
            return True 

        if any(w in text for w in ["month", "tháng", "year", "năm", "y/o"]):
            return False

        # Weeks
        w_match = re.search(r"(\d+)\s*(?:w|week|tuần)", text)
        if w_match:
            weeks = int(w_match.group(1))
            if weeks * 7 > MAX_AGE_DAYS:
                return False
            return True

        # Days
        d_match = re.search(r"(\d+)\s*(?:d|day|ngày)", text)
        if d_match:
            days = int(d_match.group(1))
            if days > MAX_AGE_DAYS:
                return False
            return True

        # Year check for absolute dates (e.g. "May 8, 2023")
        year_match = re.search(r"20\d{2}", text)
        if year_match and year_match.group(0) != str(datetime.datetime.now().year):
            return False

        return True

    @staticmethod
    def _infer_condition(text: str) -> str:
        """Infer new/used from title or post text."""
        text_l = text.lower()
        used_signals = ["cũ", "cu", "đã dùng", "second hand", "secondhand", "used",
                        "like new", "99%", "98%", "qua sử dụng"]
        new_signals = ["mới", "new", "fullbox", "full box", "chưa dùng", "bh theo máy", "bảo hành"]
        for sig in used_signals:
            if sig in text_l:
                return "used"
        for sig in new_signals:
            if sig in text_l:
                return "new"
        return "unknown"
