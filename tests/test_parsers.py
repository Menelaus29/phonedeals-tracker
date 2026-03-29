import pytest
import datetime
import time
from crawlers.facebook import FacebookCrawler

class TestFacebookPriceParser:
    def test_fractional_shorthand(self):
        # 1-digit fraction (100k)
        assert FacebookCrawler._parse_price_from_text("bán máy 8tr2 nha") == 8_200_000
        assert FacebookCrawler._parse_price_from_text("8 củ 2") == 8_200_000
        assert FacebookCrawler._parse_price_from_text("8 m 2") == 8_200_000
        assert FacebookCrawler._parse_price_from_text("Máy 8triệu9") == 8_900_000
        assert FacebookCrawler._parse_price_from_text("10tr5") == 10_500_000
        
        # 2-digit fraction (10k)
        assert FacebookCrawler._parse_price_from_text("bán máy 8tr25") == 8_250_000
        assert FacebookCrawler._parse_price_from_text("18tr99") == 18_990_000
        
        # 3-digit fraction (1k)
        assert FacebookCrawler._parse_price_from_text("bán 8tr250k") == 8_250_000
        assert FacebookCrawler._parse_price_from_text("8 củ 990") == 8_990_000

    def test_standard_shorthand(self):
        assert FacebookCrawler._parse_price_from_text("8tr") == 8_000_000
        assert FacebookCrawler._parse_price_from_text("18.5 triệu") == 18_500_000
        assert FacebookCrawler._parse_price_from_text("10,3 tr") == 10_300_000
        assert FacebookCrawler._parse_price_from_text("10 củ") == 10_000_000

    def test_explicit_amounts(self):
        assert FacebookCrawler._parse_price_from_text("Giá 18.500.000đ") == 18_500_000
        assert FacebookCrawler._parse_price_from_text("18,500,000 vnd") == 18_500_000
        assert FacebookCrawler._parse_price_from_text("18500000 ₫") == 18_500_000
        assert FacebookCrawler._parse_price_from_text("18.500.000") == 18_500_000

    def test_ignores_small_numbers(self):
        # Avoid treating versions, dates, etc. as sizes
        assert FacebookCrawler._parse_price_from_text("bán máy iphone 15 pro") == 0
        assert FacebookCrawler._parse_price_from_text("bản 256gb") == 0

class TestFacebookTimeConstraint:
    def test_recent_strings(self):
        assert FacebookCrawler._is_recent_enough("Just now") is True
        assert FacebookCrawler._is_recent_enough("2 hrs") is True
        assert FacebookCrawler._is_recent_enough("Yesterday") is True
        assert FacebookCrawler._is_recent_enough("Listed 2 days ago") is True
        assert FacebookCrawler._is_recent_enough("hôm qua") is True

    def test_week_limits(self):
        # 1 week = 7 days (allowed, max length is 15 usually)
        assert FacebookCrawler._is_recent_enough("1 w") is True
        assert FacebookCrawler._is_recent_enough("1 tuần") is True
        
        # 3 weeks = 21 days (blocked)
        assert FacebookCrawler._is_recent_enough("3 w") is False
        assert FacebookCrawler._is_recent_enough("3 weeks ago") is False

    def test_month_and_year(self):
        assert FacebookCrawler._is_recent_enough("1 month") is False
        assert FacebookCrawler._is_recent_enough("1 tháng") is False
        assert FacebookCrawler._is_recent_enough("2 years") is False

    def test_absolute_dates(self):
        # Date from a prior year 
        assert FacebookCrawler._is_recent_enough("May 8, 2023") is False
        # Missing year usually defaults to current year in FB, so allowed
        assert FacebookCrawler._is_recent_enough("May 8") is True
