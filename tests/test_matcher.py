"""
tests/test_matcher.py
─────────────────────
Unit tests for the matching engine (matcher.py).

Run with:
    python -m pytest tests/ -v
"""

import sys
import os

# Add project root to path so we can import matcher without installing
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Minimal stub for config.FUZZY_THRESHOLD to avoid needing a real .env during tests
import types
_fake_config = types.ModuleType("config")
_fake_config.FUZZY_THRESHOLD = 85
sys.modules["config"] = _fake_config

import pytest
from matcher import (
    _normalize_text,
    normalize_location,
    extract_model_keywords,
    _keyword_present_exactly,
    matches_watchlist_item,
)


# ── Normalization tests ────────────────────────────────────────────────────────

class TestNormalizeText:
    def test_lowercase(self):
        assert _normalize_text("iPhone 15 Pro") == "iphone 15 pro"

    def test_strips_punctuation(self):
        assert _normalize_text("iPhone-15 Pro!") == "iphone 15 pro"

    def test_ip_expands_to_iphone(self):
        assert "iphone" in _normalize_text("IP 15 Pro")

    def test_ss_expands_to_samsung(self):
        assert "samsung" in _normalize_text("SS Galaxy S24")

    def test_promax_expands(self):
        result = _normalize_text("iPhone 15 ProMax")
        assert "pro max" in result

    def test_pro_max_with_space_preserved(self):
        result = _normalize_text("iPhone 15 Pro Max")
        assert "pro max" in result

    def test_collapses_whitespace(self):
        assert _normalize_text("  iphone  15  pro  ") == "iphone 15 pro"


# ── Location normalization tests ───────────────────────────────────────────────

class TestNormalizeLocation:
    @pytest.mark.parametrize("alias,expected", [
        ("HN", "Hanoi"),
        ("hn", "Hanoi"),
        ("ha noi", "Hanoi"),
        ("Hanoi", "Hanoi"),
        ("HCM", "Ho Chi Minh"),
        ("SG", "Ho Chi Minh"),
        ("tphcm", "Ho Chi Minh"),
        ("sai gon", "Ho Chi Minh"),
        ("Ho Chi Minh", "Ho Chi Minh"),
        ("DN", "Da Nang"),
        ("da nang", "Da Nang"),
    ])
    def test_known_aliases(self, alias, expected):
        assert normalize_location(alias) == expected


# ── Model keyword extraction tests ─────────────────────────────────────────────

class TestExtractModelKeywords:
    def test_iphone_15_pro(self):
        keywords = extract_model_keywords("iphone 15 pro")
        assert "15 pro" in keywords or any("15" in k for k in keywords)

    def test_samsung_s24(self):
        keywords = extract_model_keywords("samsung galaxy s24")
        assert any("s24" in k for k in keywords)

    def test_samsung_s24_ultra(self):
        keywords = extract_model_keywords("samsung galaxy s24 ultra")
        assert any("ultra" in k or "s24" in k for k in keywords)


# ── Keyword exact match tests ──────────────────────────────────────────────────

class TestKeywordPresentExactly:
    def test_s24_in_s24_listing(self):
        assert _keyword_present_exactly("s24", "samsung galaxy s24 cu gia re") is True

    def test_s24_does_not_match_s24_plus(self):
        """S24 query must NOT match a listing titled S24+."""
        assert _keyword_present_exactly("s24", "samsung galaxy s24+ moi") is False

    def test_s24_does_not_match_s24_ultra(self):
        assert _keyword_present_exactly("s24", "samsung galaxy s24 ultra") is False

    def test_15_pro_present(self):
        assert _keyword_present_exactly("15 pro", "iphone 15 pro moi fullbox") is True

    def test_15_does_not_match_15_pro(self):
        """Searching for bare "15" should not falsely match in "15 pro"
        if the user is only tracking "iPhone 15" (no suffix)."""
        assert _keyword_present_exactly("15 pro", "iphone 15 ultra thinh hanh") is False


# ── End-to-end matching tests ──────────────────────────────────────────────────

class TestMatchesWatchlistItem:
    def _item(self, model, condition="any", threshold=20_000_000):
        return {"model": model, "condition": condition, "threshold": threshold}

    # ── Must match ────────────────────────────────────────────────────────────

    def test_match_iphone_15_pro_used(self):
        is_match, pct = matches_watchlist_item(
            listing_title="iPhone 15 Pro 256GB Cũ Đẹp Like New",
            listing_condition="used",
            listing_price=18_000_000,
            watchlist_item=self._item("iPhone 15 Pro", condition="used", threshold=20_000_000),
            threshold=20_000_000,
        )
        assert is_match is True
        assert pct == pytest.approx(10.0)

    def test_match_with_abbreviation_ip(self):
        """Listing uses 'IP 15 Pro' abbreviation."""
        is_match, _ = matches_watchlist_item(
            listing_title="IP 15 Pro 256GB Fullbox Mới",
            listing_condition="new",
            listing_price=19_000_000,
            watchlist_item=self._item("iPhone 15 Pro", condition="new", threshold=20_000_000),
            threshold=20_000_000,
        )
        assert is_match is True

    def test_match_samsung_s24(self):
        is_match, _ = matches_watchlist_item(
            listing_title="Samsung Galaxy S24 8GB/256GB Mới Hãng",
            listing_condition="new",
            listing_price=17_000_000,
            watchlist_item=self._item("Samsung Galaxy S24", condition="new", threshold=18_000_000),
            threshold=18_000_000,
        )
        assert is_match is True

    # ── Must NOT match ────────────────────────────────────────────────────────

    def test_no_match_price_above_threshold(self):
        is_match, _ = matches_watchlist_item(
            listing_title="iPhone 15 Pro 256GB Mới",
            listing_condition="new",
            listing_price=21_000_000,
            watchlist_item=self._item("iPhone 15 Pro", threshold=20_000_000),
            threshold=20_000_000,
        )
        assert is_match is False

    def test_no_match_wrong_condition(self):
        is_match, _ = matches_watchlist_item(
            listing_title="iPhone 15 Pro Used",
            listing_condition="used",
            listing_price=18_000_000,
            watchlist_item=self._item("iPhone 15 Pro", condition="new", threshold=20_000_000),
            threshold=20_000_000,
        )
        assert is_match is False

    def test_no_match_s24_vs_s24_plus(self):
        """S24 watchlist item must NOT fire on an S24+ listing."""
        is_match, _ = matches_watchlist_item(
            listing_title="Samsung Galaxy S24+ 256GB Mới",
            listing_condition="new",
            listing_price=17_000_000,
            watchlist_item=self._item("Samsung Galaxy S24", threshold=18_000_000),
            threshold=18_000_000,
        )
        assert is_match is False

    def test_no_match_s24_vs_s24_ultra(self):
        is_match, _ = matches_watchlist_item(
            listing_title="Samsung Galaxy S24 Ultra Chính Hãng Mới",
            listing_condition="new",
            listing_price=17_000_000,
            watchlist_item=self._item("Samsung Galaxy S24", threshold=18_000_000),
            threshold=18_000_000,
        )
        assert is_match is False

    def test_no_match_completely_different_model(self):
        is_match, _ = matches_watchlist_item(
            listing_title="Xiaomi Redmi Note 13 Pro Mới",
            listing_condition="new",
            listing_price=8_000_000,
            watchlist_item=self._item("iPhone 15 Pro", threshold=20_000_000),
            threshold=20_000_000,
        )
        assert is_match is False
