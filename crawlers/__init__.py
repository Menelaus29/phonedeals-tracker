"""
crawlers/__init__.py
"""
from .chotot import ChototCrawler
from .shopee import ShopeeCrawler
from .tiki import TikiCrawler
from .lazada import LazadaCrawler
from .facebook import FacebookCrawler

__all__ = [
    "ChototCrawler",
    "ShopeeCrawler",
    "TikiCrawler",
    "LazadaCrawler",
    "FacebookCrawler",
]
