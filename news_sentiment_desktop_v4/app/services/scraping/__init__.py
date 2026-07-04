from .body_scraper import BodyScraper, FetchOutcome

# PlaywrightScraper 內部採延遲 import（playwright/gne 未安裝時仍可 import 本 package）
from .playwright_scraper import PlaywrightScraper

__all__ = ["BodyScraper", "FetchOutcome", "PlaywrightScraper"]
