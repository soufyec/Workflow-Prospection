import random
import time
from typing import Optional

import requests

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36 Edg/119.0.0.0",
]


def get_random_headers() -> dict:
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }


def fetch_page(
    url: str,
    delay_min: float = 1.5,
    delay_max: float = 4.0,
    max_retries: int = 3,
    timeout: int = 15,
    session: Optional[requests.Session] = None,
) -> Optional[str]:
    """
    Fetch a URL with random delay, user-agent rotation, and exponential backoff.
    Returns HTML string or None on failure.
    """
    requester = session or requests
    for attempt in range(max_retries):
        time.sleep(random.uniform(delay_min, delay_max))
        try:
            response = requester.get(
                url,
                headers=get_random_headers(),
                timeout=timeout,
                allow_redirects=True,
            )
            if response.status_code == 200:
                return response.text
            elif response.status_code == 429:
                wait = 2 ** (attempt + 2)
                print(f"    [RATE LIMIT] Waiting {wait}s before retry...")
                time.sleep(wait)
            elif response.status_code in (403, 404):
                return None
        except requests.RequestException as e:
            if attempt < max_retries - 1:
                time.sleep(2**attempt)
            else:
                print(f"    [ERROR] Failed to fetch {url}: {e}")
    return None


def fetch_page_playwright(url: str) -> Optional[str]:
    """
    Fallback fetch using Playwright headless Chromium for JS-rendered sites.
    Returns HTML string or None on failure.
    """
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(user_agent=random.choice(USER_AGENTS))
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(random.randint(1500, 3000))
            content = page.content()
            browser.close()
            return content
    except Exception as e:
        print(f"    [PLAYWRIGHT ERROR] {url}: {e}")
        return None


def needs_js_rendering(html: Optional[str]) -> bool:
    """
    Heuristic: returns True if the page likely requires JS to render content.
    Triggers when HTML is too short or contains very few links.
    """
    if html is None or len(html) < 1000:
        return True
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    return len(soup.find_all("a", href=True)) < 3
