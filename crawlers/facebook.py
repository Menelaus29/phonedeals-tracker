"""
crawlers/facebook.py
────────────────────
Scrapes:
  1. Facebook Marketplace (filtered by location)
  2. Facebook Group post search results + comments inside those posts
  3. Public Facebook Page / Shop feeds

Authentication:
  - Uses a persistent Playwright browser context stored in BROWSER_DATA_DIR.
  - On the very first run (if the directory is empty/doesn't exist), the bot
    opens a visible browser window and waits for the user to log in manually.
    After login, it saves the session to disk automatically.
  - All subsequent runs reuse the saved session headlessly.

Facebook Marketplace URL pattern (Vietnam, by location):
  https://www.facebook.com/marketplace/<city_code>/search?query=<q>

Facebook Groups URL pattern (posts + comments):
  <group_url>/search/?q=<query>

Facebook Page/Shop URL pattern:
  <page_url>  (scrolled feed, posts parsed for price)
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
import config

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
MAX_SCROLL_ROUNDS = 5        # How many times to scroll down + wait for more results
MAX_COMMENT_POSTS = 8        # Max posts to open and scrape comments from per group search
MAX_COMMENTS_PER_POST = 20   # Max comments to parse per post
SCROLL_PAUSE = 2.5      # Seconds between scrolls
# URL fragments that indicate we've been redirected away from the target page
_LOGIN_URL_FRAGMENTS = ("/login", "/checkpoint", "/recover")
# A selector that only exists when logged in (the Marketplace icon in the nav)
_LOGGED_IN_SELECTOR = "a[href*='/marketplace']"

class FacebookCrawler(BaseCrawler):
    """
    NOTE: This crawler requires its OWN persistent browser context,
    managed separately in main.py. The page passed here should already
    be authenticated with Facebook.
    """
    SOURCE = "facebook"

    def crawl(self) -> Generator[Listing, None, None]:
        # bail out early if session is dead
        if not self._check_auth():
            log.error("[Facebook] Aborting crawl — session invalid.")
            return
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

            # ── Facebook Groups (posts + comments) ────────────────────────────
            for group_url in config.FB_GROUP_URLS:
                search_url = f"{group_url.rstrip('/')}/search/?q={quote_plus(query)}"
                log.info("[Facebook] Group search: '%s' at %s", query, group_url)
                yield from self._scrape_marketplace_or_group(search_url, is_group=True)

            # ── Facebook Pages / Shops ────────────────────────────────────────
            for page_url in config.FB_PAGE_URLS:
                log.info("[Facebook] Page/Shop feed: '%s' at %s", query, page_url)
                yield from self._scrape_page_posts(page_url, query)

            time.sleep(3)

    def _scrape_marketplace_or_group(self, url: str, is_group: bool) -> Generator[Listing, None, None]:
        if not self.safe_goto(url, wait_until="domcontentloaded"):
            return

        # ── Mid-crawl session drop check ──────────────────────────────────────
        current_url = self.page.url
        if any(frag in current_url for frag in _LOGIN_URL_FRAGMENTS):
            log.warning("[Facebook] Redirected to login mid-crawl — session dropped.")
            self._send_session_alert(reason="session_expired")
            return

        # Give React a moment to render
        time.sleep(3)
        self._dismiss_login_popup()

        seen_urls: set[str] = set()
        comment_post_urls: list[str] = []  # collect post URLs to dive into for comments

        for _ in range(MAX_SCROLL_ROUNDS):
            if is_group:
                cards = self._extract_group_posts()
            else:
                cards = self._extract_marketplace_cards()

            for listing in cards:
                if listing.url not in seen_urls:
                    seen_urls.add(listing.url)
                    yield listing
                    # Queue posts for comment scraping
                    if is_group and listing.url not in comment_post_urls:
                        comment_post_urls.append(listing.url)

            # Scroll down
            self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(SCROLL_PAUSE)

        # ── Dive into posts to scrape comments ───────────────────────────────
        if is_group:
            for post_url in comment_post_urls[:MAX_COMMENT_POSTS]:
                if post_url in seen_urls:
                    # We already have the post itself; now get comments too
                    for listing in self._extract_post_comments(post_url):
                        if listing.url not in seen_urls:
                            seen_urls.add(listing.url)
                            yield listing

    def _check_auth(self) -> bool:
        """
        Navigate to Facebook home and verify the session is still alive.
        Returns True if authenticated, False if session has expired or CAPTCHA hit.
        Fires a Telegram alert if auth is lost.
        """
        import notifier

        log.info("[Facebook] Checking session validity...")
        try:
            self.page.goto("https://www.facebook.com", wait_until="domcontentloaded", timeout=20_000)
        except PWTimeout:
            log.error("[Facebook] Timed out loading Facebook home — network issue?")
            return True  # Don't treat a network blip as a session expiry

        current_url = self.page.url

        # Redirected to login or checkpoint
        if any(frag in current_url for frag in _LOGIN_URL_FRAGMENTS):
            log.warning("[Facebook] Session expired — redirected to %s", current_url)
            self._send_session_alert(reason="session_expired")
            return False

        # Still on facebook.com but Marketplace link missing — CAPTCHA or restricted
        try:
            self.page.wait_for_selector(_LOGGED_IN_SELECTOR, timeout=5_000)
        except PWTimeout:
            log.warning("[Facebook] Logged-in UI not found — possible CAPTCHA or account restriction.")
            self._send_session_alert(reason="captcha")
            return False

        log.info("[Facebook] Session OK.")
        return True


    def _send_session_alert(self, reason: str) -> None:
        """Send a Telegram alert telling the user to re-authenticate."""
        import notifier

        if reason == "captcha":
            message = (
                "⚠️ *Facebook CAPTCHA detected\\.* \n\n"
                "The crawler cannot continue\\. To fix:\n"
                "1\\. Set `HEADLESS=false` in your `.env`\n"
                "2\\. Restart `python main\\.py`\n"
                "3\\. Solve the CAPTCHA in the browser window\n"
                "4\\. Set `HEADLESS=true` again"
            )
        else:
            message = (
                "⚠️ *Facebook session expired\\.* \n\n"
                "The crawler cannot continue\\. To fix:\n"
                "1\\. Delete the `browser\\_data` folder\n"
                "2\\. Restart `python main\\.py`\n"
                "3\\. Log in manually in the browser window that opens"
            )

        try:
            import requests
            resp = requests.post(
                f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage",
                json={
                    "chat_id": config.TELEGRAM_CHAT_ID,
                    "text": message,
                    "parse_mode": "MarkdownV2",
                },
                timeout=15,
            )
            resp.raise_for_status()
            log.info("[Facebook] Session alert sent to Telegram.")
        except Exception as exc:
            log.error("[Facebook] Failed to send session alert: %s", exc)

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

    def _extract_post_comments(self, post_url: str) -> list[Listing]:
        """
        Navigate to a group post URL and extract top-level comments that
        contain a price.  Each comment generates a Listing whose URL is the
        post URL with a synthetic '#comment-<index>' fragment so it is
        deduplicated correctly.
        """
        results = []
        try:
            if not self.safe_goto(post_url, wait_until="domcontentloaded"):
                return results

            current_url = self.page.url
            if any(frag in current_url for frag in _LOGIN_URL_FRAGMENTS):
                return results

            time.sleep(3)
            self._dismiss_login_popup()

            # Expand comments if a "View more comments" button is present
            for _ in range(3):
                try:
                    more_btn = self.page.query_selector(
                        "div[role='button'][tabindex='0']:has-text('View more comments'), "
                        "div[role='button'][tabindex='0']:has-text('Xem thêm bình luận')"
                    )
                    if more_btn:
                        more_btn.click()
                        time.sleep(1.5)
                    else:
                        break
                except Exception:
                    break

            # Each top-level comment is wrapped in a role='article' inside the
            # comments section; fall back to ul[role='list'] > li structure.
            comment_els = self.page.query_selector_all(
                "ul[role='list'] > li div[dir='auto']"
            )
            if not comment_els:
                comment_els = self.page.query_selector_all(
                    "div[role='article'] div[dir='auto']"
                )

            seen_texts: set[str] = set()
            idx = 0
            for el in comment_els[:MAX_COMMENTS_PER_POST * 3]:  # allow some duds
                listing = self._parse_comment(el, post_url, idx)
                if listing:
                    text_key = listing.title[:60]
                    if text_key not in seen_texts:
                        seen_texts.add(text_key)
                        results.append(listing)
                        idx += 1
                        if idx >= MAX_COMMENTS_PER_POST:
                            break
        except Exception as exc:  # noqa: BLE001
            log.debug("[Facebook] Comment extraction error for %s: %s", post_url, exc)
        return results

    def _parse_comment(self, el, post_url: str, idx: int) -> Listing | None:
        """Parse a single comment element into a Listing, or None if no price found."""
        try:
            text = el.inner_text().strip()
            if not text or len(text) < 5:
                return None

            price = self._parse_price_from_text(text)
            if price == 0:
                return None

            title = text[:200].replace("\n", " ").strip()
            condition = self._infer_condition(text)
            # Unique URL per comment so DB deduplication works
            comment_url = f"{post_url}#comment-{idx}"

            return Listing(
                url=comment_url,
                source="facebook",
                title=f"[Comment] {title}",
                price=price,
                condition=condition,
                location=self.location,
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("[Facebook] Comment parse error: %s", exc)
            return None

    # ── Page / Shop feed extraction ────────────────────────────────────────────

    def _scrape_page_posts(self, page_url: str, query: str) -> Generator[Listing, None, None]:
        """
        Scrape posts from a public Facebook Page/Shop.
        Posts are scrolled through and only those matching the query keywords
        and containing a price are yielded.
        """
        if not self.safe_goto(page_url.rstrip("/"), wait_until="domcontentloaded"):
            return

        current_url = self.page.url
        if any(frag in current_url for frag in _LOGIN_URL_FRAGMENTS):
            log.warning("[Facebook] Redirected to login accessing page %s", page_url)
            return

        time.sleep(3)
        self._dismiss_login_popup()

        seen_urls: set[str] = set()
        # Keywords from the query to filter relevant posts
        kw_tokens = [w.lower() for w in query.split() if len(w) > 2]

        for _ in range(MAX_SCROLL_ROUNDS):
            articles = self.page.query_selector_all("div[role='article']")
            for article in articles:
                listing = self._parse_page_post(article, page_url, kw_tokens)
                if listing and listing.url not in seen_urls:
                    seen_urls.add(listing.url)
                    yield listing

            self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(SCROLL_PAUSE)

    def _parse_page_post(self, article, page_url: str, kw_tokens: list[str]) -> Listing | None:
        """Parse a single post article element from a Page/Shop feed."""
        try:
            # Try to find a link to the specific post
            link_el = (
                article.query_selector("a[href*='/posts/']")
                or article.query_selector("a[href*='/permalink/']")
                or article.query_selector("a[href*='story_fbid']")
            )
            href = link_el.get_attribute("href") if link_el else ""
            if href:
                url = href.split("?")[0]
                if url.startswith("/"):
                    url = "https://www.facebook.com" + url
            else:
                # Fall back to page URL with a fingerprint from content
                content_hash = abs(hash(article.inner_text()[:100])) % 10_000_000
                url = f"{page_url.rstrip('/')}#post-{content_hash}"

            # Get text content
            content_el = (
                article.query_selector("div[data-ad-comet-preview='message']")
                or article.query_selector("div[dir='auto']")
            )
            if not content_el:
                return None

            text = content_el.inner_text().strip()
            if not text:
                return None

            # Filter: must contain at least one query keyword
            text_lower = text.lower()
            if kw_tokens and not any(kw in text_lower for kw in kw_tokens):
                return None

            price = self._parse_price_from_text(text)
            if price == 0:
                return None

            if not self._is_recent_enough(text):
                return None

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
            log.debug("[Facebook] Page post parse error: %s", exc)
            return None

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

        text = date_text.lower().strip()
        if not text:
            return True 

        if any(w in text for w in ["month", "tháng", "year", "năm", "y/o"]):
            return False

        # Weeks
        w_match = re.search(r"(\d+)\s*(?:w|week|tuần)", text)
        if w_match:
            weeks = int(w_match.group(1))
            if weeks * 7 > config.MAX_AGE_DAYS:
                return False
            return True

        # Days
        d_match = re.search(r"(\d+)\s*(?:d|day|ngày)", text)
        if d_match:
            days = int(d_match.group(1))
            if days > config.MAX_AGE_DAYS:
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
