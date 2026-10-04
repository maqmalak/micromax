"""Moved to mm_core.crm_scraper (with the CRM Prospect Scrape doctype), so every site has the Prospect Scraper.

Kept so the old API path (micromax.crm_scraper.scrape_urls) keeps working: the function is the same object,
so it stays whitelisted under this name too.
"""

from mm_core.crm_scraper import *  # noqa: F401,F403
from mm_core.crm_scraper import scrape_urls  # noqa: F401
