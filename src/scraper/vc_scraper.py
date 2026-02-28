"""
Stage 1: Discover portfolio companies from curated VC websites.

Two sources:
  1. VC portfolio pages (static scraping with Playwright fallback)
  2. VC LinkedIn posts (via linkedin_scraper, requires credentials)

Results are merged and deduplicated by domain, then persisted to pipeline.json.
"""

import json
import re
from typing import Optional
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from config.settings import PIPELINE_PATH, SENT_LOG_PATH, VC_LIST_PATH
from src.utils.deduplication import filter_new_companies
from src.utils.http_client import fetch_page, fetch_page_playwright, needs_js_rendering
from src.utils.pipeline_state import append_to_state_list, load_state

EXCLUDED_PATTERNS = re.compile(
    r"linkedin|twitter|instagram|facebook|crunchbase|angellist|angel\.co"
    r"|careers|jobs|privacy|terms|contact|about|team|blog|news|press"
    r"|medium\.com|techcrunch|youtube|vimeo|spotify",
    re.IGNORECASE,
)


def load_vc_list(path: str = VC_LIST_PATH) -> list:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _build_record(name: str, website: str, vc_entry: dict, source: str = "portfolio_page") -> dict:
    return {
        "company_name": name.strip(),
        "website": website.rstrip("/"),
        "vc_source": vc_entry["name"],
        "funding_stage": vc_entry.get("typical_stage", "unknown"),
        "industry": vc_entry.get("focus_sector", ""),
        "discovery_source": source,
        "post_text": "",
        "stakeholder_email": None,
        "stakeholder_name": None,
        "email_source_url": None,
        "email_subject": None,
        "email_body": None,
        "review_status": "pending",
    }


def extract_companies_from_html(html: str, vc_entry: dict, base_url: str) -> list:
    """
    Extract company name + website URL from a VC portfolio page HTML.

    Strategy:
    1. If vc_entry has a "company_selector", use it as a CSS selector.
    2. Otherwise apply heuristic: find external <a> links with 1–6 word text
       that are not social media, careers, or utility pages.
    """
    soup = BeautifulSoup(html, "html.parser")
    vc_domain = urlparse(base_url).netloc
    companies = []

    selector = vc_entry.get("company_selector")
    if selector:
        for el in soup.select(selector):
            link = el.find("a", href=True)
            name = el.get_text(strip=True)
            if link and name:
                href = urljoin(base_url, link["href"])
                if urlparse(href).netloc != vc_domain:
                    companies.append(_build_record(name, href, vc_entry))
        return _dedup_by_domain(companies)

    # Generic heuristic
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = a.get_text(strip=True)

        if not href.startswith("http"):
            href = urljoin(base_url, href)
        if not href.startswith("http"):
            continue

        parsed = urlparse(href)
        if parsed.netloc == vc_domain:
            continue
        if EXCLUDED_PATTERNS.search(href) or EXCLUDED_PATTERNS.search(text):
            continue

        word_count = len(text.split())
        if 1 <= word_count <= 6 and len(text) > 2:
            companies.append(_build_record(text, href, vc_entry))

    return _dedup_by_domain(companies)


def _dedup_by_domain(companies: list) -> list:
    """Remove duplicate companies by their root domain."""
    seen = set()
    unique = []
    for c in companies:
        domain = urlparse(c["website"]).netloc
        if domain and domain not in seen:
            seen.add(domain)
            unique.append(c)
    return unique


def _scrape_vc_portfolio(vc: dict) -> list:
    """Fetch and parse a single VC's portfolio page. Returns list of companies."""
    url = vc["portfolio_url"]
    print(f"  [PORTFOLIO] {vc['name']} → {url}")

    html = fetch_page(url)
    if needs_js_rendering(html) or vc.get("scrape_strategy") == "js":
        print(f"    → Using Playwright for JS rendering")
        html = fetch_page_playwright(url)

    if not html:
        print(f"    [WARN] Could not fetch portfolio page")
        return []

    companies = extract_companies_from_html(html, vc, url)
    print(f"    → Found {len(companies)} companies")
    return companies


def discover_companies(vc_list: Optional[list] = None) -> list:
    """
    Main entry point for Stage 1.

    1. For each VC: scrape portfolio page + LinkedIn posts (if enabled)
    2. Merge and deduplicate results
    3. Persist incrementally to pipeline.json (crash-safe)
    4. Filter out already-contacted companies
    5. Return all newly discovered companies
    """
    if vc_list is None:
        vc_list = load_vc_list()

    state = load_state(PIPELINE_PATH)
    already_processed_vcs = {c["vc_source"] for c in state.get("discovered", [])}

    # --- Portfolio page scraping ---
    portfolio_companies = []
    for vc in vc_list:
        if vc["name"] in already_processed_vcs:
            print(f"  [SKIP] {vc['name']} already processed")
            continue
        companies = _scrape_vc_portfolio(vc)
        if companies:
            append_to_state_list(PIPELINE_PATH, "discovered", companies)
            portfolio_companies.extend(companies)

    # --- LinkedIn post scraping ---
    print("\n  Starting LinkedIn post scraping...")
    try:
        from src.scraper.linkedin_scraper import discover_from_linkedin
        linkedin_companies = discover_from_linkedin(vc_list)
        if linkedin_companies:
            append_to_state_list(PIPELINE_PATH, "discovered", linkedin_companies)
            print(f"  [LINKEDIN] Added {len(linkedin_companies)} companies from posts")
    except ImportError:
        print("  [SKIP] LinkedIn scraper unavailable")

    # --- News RSS feed scraping ---
    print("\n  Starting news feed scraping...")
    try:
        from src.scraper.news_scraper import discover_from_news
        news_companies = discover_from_news()
        if news_companies:
            append_to_state_list(PIPELINE_PATH, "discovered", news_companies)
            print(f"  [NEWS] Added {len(news_companies)} companies from news feeds")
    except Exception as e:
        print(f"  [SKIP] News feed scraping failed: {e}")

    # Load full discovered list from state (includes previous runs)
    state = load_state(PIPELINE_PATH)
    all_discovered = state.get("discovered", [])

    # Filter already-contacted companies
    new_companies = filter_new_companies(all_discovered, SENT_LOG_PATH)
    print(f"\n  Total discovered: {len(all_discovered)} | New (not yet contacted): {len(new_companies)}")
    return new_companies
