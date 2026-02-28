"""
LinkedIn post scraper.

Uses Playwright with the user's own LinkedIn credentials to browse VC
company pages and extract funding announcement posts. Saves session
cookies to avoid re-authentication on every run.

Note: This mimics manual browsing. LinkedIn's TOS prohibits automated
scraping — use at your own discretion.
"""

import json
import os
import re
import time
import random
from typing import Optional
from urllib.parse import urlparse

from config.settings import (
    LINKEDIN_EMAIL,
    LINKEDIN_PASSWORD,
    LINKEDIN_SESSION_PATH,
)

FUNDING_KEYWORDS = [
    "raised", "raise", "funding", "funded", "investment", "invested",
    "seed", "series a", "pre-seed", "preseed", "series b",
    "backed", "portfolio", "excited to announce", "proud to support",
    "proud to invest", "thrilled to announce", "welcome to our portfolio",
    "new investment", "closed a", "million", "€", "$",
]

# Regex to find URLs in post text
URL_REGEX = re.compile(r"https?://[^\s\"'<>]+")

# Proper noun heuristic: 1–5 consecutive capitalized words
PROPER_NOUN_REGEX = re.compile(r"\b([A-Z][a-z]+(?:\s[A-Z][a-z]+){0,4})\b")

SOCIAL_MEDIA_DOMAINS = {
    "linkedin.com", "twitter.com", "x.com", "facebook.com",
    "instagram.com", "youtube.com", "tiktok.com",
}


def _is_funding_post(text: str) -> bool:
    """Return True if post text contains funding-related keywords."""
    text_lower = text.lower()
    return any(kw in text_lower for kw in FUNDING_KEYWORDS)


def _extract_urls_from_text(text: str) -> list:
    """Extract all HTTP URLs from post text."""
    return [
        url.rstrip(".,;)\"'")
        for url in URL_REGEX.findall(text)
        if not any(domain in url for domain in SOCIAL_MEDIA_DOMAINS)
    ]


def _domain_from_url(url: str) -> str:
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lstrip("www.")
        return domain
    except Exception:
        return ""


def _load_session(path: str) -> Optional[list]:
    """Load saved LinkedIn cookies from JSON file."""
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _save_session(path: str, cookies: list) -> None:
    """Save LinkedIn session cookies to JSON file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cookies, f, indent=2)


def _is_logged_in(page) -> bool:
    """Check if current page shows a logged-in LinkedIn state."""
    return "feed" in page.url or page.query_selector("div.feed-identity-module") is not None


def login_linkedin(page) -> bool:
    """
    Log in to LinkedIn using credentials from .env.
    Tries saved session cookies first; falls back to password login.
    Returns True on success, False on failure.
    """
    if not LINKEDIN_EMAIL or not LINKEDIN_PASSWORD:
        print("    [WARN] LINKEDIN_EMAIL or LINKEDIN_PASSWORD not set in .env — skipping LinkedIn scraping")
        return False

    # Try cached session
    cookies = _load_session(LINKEDIN_SESSION_PATH)
    if cookies:
        page.goto("https://www.linkedin.com", wait_until="domcontentloaded", timeout=15000)
        page.context.add_cookies(cookies)
        page.reload(wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(2000)
        if _is_logged_in(page):
            print("    [LINKEDIN] Using saved session")
            return True

    # Full login
    print("    [LINKEDIN] Logging in...")
    page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded", timeout=15000)
    page.wait_for_timeout(random.randint(1000, 2000))

    email_field = page.query_selector("#username")
    password_field = page.query_selector("#password")
    if not email_field or not password_field:
        print("    [LINKEDIN ERROR] Login form not found")
        return False

    # Type credentials with human-like delays
    for char in LINKEDIN_EMAIL:
        email_field.type(char, delay=random.randint(40, 100))
    page.wait_for_timeout(random.randint(300, 700))
    for char in LINKEDIN_PASSWORD:
        password_field.type(char, delay=random.randint(40, 100))
    page.wait_for_timeout(random.randint(300, 600))
    password_field.press("Enter")

    # Wait for either feed or security challenge
    try:
        page.wait_for_url("**/feed**", timeout=15000)
    except Exception:
        # Security challenge — pause for user to resolve
        print("\n  [LINKEDIN] Security check detected. Please complete the verification in the browser window.")
        print("  Press Enter here once you have passed the verification...")
        input()

    if not _is_logged_in(page):
        print("    [LINKEDIN ERROR] Login failed")
        return False

    # Save session cookies
    _save_session(LINKEDIN_SESSION_PATH, page.context.cookies())
    print("    [LINKEDIN] Logged in and session saved")
    return True


def scrape_vc_linkedin_posts(page, vc_linkedin_url: str) -> list:
    """
    Scrape recent posts from a VC's LinkedIn company page.
    Scrolls to load ~30 posts. Returns list of post text strings.
    """
    try:
        page.goto(vc_linkedin_url, wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(random.randint(2000, 4000))

        posts = []
        for _ in range(5):
            page.evaluate("window.scrollBy(0, window.innerHeight * 2)")
            page.wait_for_timeout(random.randint(1500, 3000))

        post_elements = page.query_selector_all(
            "div.feed-shared-update-v2__description-wrapper, "
            "span.break-words, "
            "div.update-components-text"
        )
        for el in post_elements:
            text = el.inner_text().strip()
            if text:
                posts.append(text)

        return posts
    except Exception as e:
        print(f"    [LINKEDIN ERROR] Could not scrape {vc_linkedin_url}: {e}")
        return []


def extract_companies_from_posts(posts: list, vc_entry: dict) -> list:
    """
    Filter posts for funding keywords, then extract company names and URLs.
    Returns list of CompanyRecord dicts.
    """
    companies = []
    seen_domains = set()

    for post_text in posts:
        if not _is_funding_post(post_text):
            continue

        urls = _extract_urls_from_text(post_text)
        proper_nouns = PROPER_NOUN_REGEX.findall(post_text)

        # Prefer URLs found directly in the post
        for url in urls:
            domain = _domain_from_url(url)
            if domain and domain not in seen_domains:
                seen_domains.add(domain)
                # Try to match a proper noun as the company name
                company_name = proper_nouns[0] if proper_nouns else domain
                companies.append({
                    "company_name": company_name,
                    "website": f"https://{domain}" if not url.startswith("http") else url,
                    "vc_source": vc_entry["name"],
                    "funding_stage": vc_entry.get("typical_stage", "unknown"),
                    "industry": vc_entry.get("focus_sector", ""),
                    "discovery_source": "linkedin_post",
                    "post_text": post_text[:300],
                    "stakeholder_email": None,
                    "stakeholder_name": None,
                    "email_source_url": None,
                    "email_subject": None,
                    "email_body": None,
                    "review_status": "pending",
                })

        # If no URL found but we have funding keywords + company nouns, add without website
        if not urls and proper_nouns:
            name = proper_nouns[0]
            if name not in seen_domains:
                seen_domains.add(name)
                companies.append({
                    "company_name": name,
                    "website": "",
                    "vc_source": vc_entry["name"],
                    "funding_stage": vc_entry.get("typical_stage", "unknown"),
                    "industry": vc_entry.get("focus_sector", ""),
                    "discovery_source": "linkedin_post",
                    "post_text": post_text[:300],
                    "stakeholder_email": None,
                    "stakeholder_name": None,
                    "email_source_url": None,
                    "email_subject": None,
                    "email_body": None,
                    "review_status": "pending",
                })

    return companies


def discover_from_linkedin(vc_list: list) -> list:
    """
    Main entry point: scrape LinkedIn posts for all VCs that have
    scrape_linkedin=True and a linkedin_url. Returns discovered companies.
    """
    linkedin_vcs = [vc for vc in vc_list if vc.get("scrape_linkedin") and vc.get("linkedin_url")]
    if not linkedin_vcs:
        return []

    if not LINKEDIN_EMAIL or not LINKEDIN_PASSWORD:
        print("  [SKIP] LinkedIn scraping disabled — set LINKEDIN_EMAIL and LINKEDIN_PASSWORD in .env")
        return []

    all_companies = []

    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)  # Visible for CAPTCHA handling
            context = browser.new_context()
            page = context.new_page()

            if not login_linkedin(page):
                browser.close()
                return []

            for vc in linkedin_vcs:
                print(f"  [LINKEDIN] Scraping posts: {vc['name']}")
                posts = scrape_vc_linkedin_posts(page, vc["linkedin_url"])
                companies = extract_companies_from_posts(posts, vc)
                print(f"    → Found {len(companies)} funding mentions")
                all_companies.extend(companies)
                # Human-like delay between VC pages
                time.sleep(random.uniform(3, 6))

            # Save updated session
            _save_session(LINKEDIN_SESSION_PATH, context.cookies())
            browser.close()

    except Exception as e:
        print(f"  [LINKEDIN ERROR] {e}")

    return all_companies
