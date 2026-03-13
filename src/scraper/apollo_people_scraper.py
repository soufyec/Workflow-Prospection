"""
Apollo.io People Scraper — browser-based (Playwright).

Logs in to app.apollo.io using a saved session or via manual Google sign-in
(one time), then searches for decision-makers at each company domain,
and writes results to a local cache (credentials/apollo_cache.json).

The enrichment waterfall (multi_provider.py) reads from this cache so
the browser is never opened during batch enrichment.

Flow:
  1. Load/validate session cookies (credentials/apollo_session.json)
  2. If expired → open browser, user signs in with Google once
  3. For each domain: navigate to people search, extract visible contacts
  4. Save results to cache (credentials/apollo_cache.json)

Usage — run manually before enrichment:
  python -m src.scraper.apollo_people_scraper

Or call from pipeline:
  from src.scraper.apollo_people_scraper import run_apollo_scraper
  run_apollo_scraper(["sinch.com", "pleo.io", "trustpilot.com"])

Cache format (apollo_cache.json):
  {
    "sinch.com": [
      {"name": "Oscar Werner", "title": "CEO", "email": "o@sinch.com"},
      ...
    ],
    ...
  }

Note: Apollo's ToS prohibits automated scraping — use responsibly.
"""

import json
import os
import random
from typing import Optional

from config.settings import APOLLO_EMAIL, APOLLO_SESSION_PATH

APOLLO_APP_URL = "https://app.apollo.io"
APOLLO_CACHE_PATH = os.path.join(
    os.path.dirname(APOLLO_SESSION_PATH), "apollo_cache.json"
)

_DECISION_MAKER_TITLES = [
    "CEO", "Chief Executive Officer",
    "Founder", "Co-Founder",
    "CMO", "Chief Marketing Officer",
    "Marketing Director", "Head of Marketing", "VP Marketing",
    "Chief Growth Officer", "Growth Director",
    "COO", "Managing Director", "General Manager",
]


# ── Cache helpers ─────────────────────────────────────────────────────────────

def load_cache() -> dict:
    if not os.path.exists(APOLLO_CACHE_PATH):
        return {}
    try:
        with open(APOLLO_CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_cache(cache: dict) -> None:
    os.makedirs(os.path.dirname(APOLLO_CACHE_PATH) or ".", exist_ok=True)
    with open(APOLLO_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)


def get_cached_contacts(domain: str) -> Optional[list]:
    """Return cached Apollo contacts for a domain, or None if not cached."""
    return load_cache().get(domain)


# ── Session helpers ───────────────────────────────────────────────────────────

def _load_session(path: str) -> Optional[list]:
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _save_session(path: str, cookies: list) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cookies, f, indent=2)


def _is_logged_in(page) -> bool:
    url = page.url
    return (
        "app.apollo.io" in url
        and "#/login" not in url
        and "#/onboarding" not in url
        and page.query_selector(
            "[data-cy='nav-logo'], [data-testid='nav-logo'], "
            ".zp_cTA3A, nav[class*='Nav'], [class*='sidebar']"
        ) is not None
    )


# ── Login ─────────────────────────────────────────────────────────────────────

def login_apollo(page) -> bool:
    """
    Ensure the browser is authenticated at app.apollo.io.
    Tries saved session first; then prompts for manual Google sign-in.
    """
    cookies = _load_session(APOLLO_SESSION_PATH)
    if cookies:
        page.goto(APOLLO_APP_URL, wait_until="domcontentloaded", timeout=20000)
        page.context.add_cookies(cookies)
        page.reload(wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(3000)
        if _is_logged_in(page):
            print("    [APOLLO] Sesión guardada cargada OK")
            return True
        print("    [APOLLO] Sesión expirada — re-autenticando")

    print("\n" + "=" * 60)
    print("  APOLLO LOGIN")
    print(f"  Se abrirá el navegador. Inicia sesión con Google")
    print(f"  usando la cuenta: {APOLLO_EMAIL or 'tu Gmail'}")
    print("  Una vez en el dashboard de Apollo, pulsa Enter aquí.")
    print("=" * 60)

    page.goto(f"{APOLLO_APP_URL}/#/login", wait_until="domcontentloaded", timeout=20000)
    page.wait_for_timeout(2000)

    # Try clicking "Sign in with Google"
    for selector in [
        "button:has-text('Continue with Google')",
        "button:has-text('Sign in with Google')",
        "a:has-text('Google')",
        "[data-cy='google-login']",
    ]:
        btn = page.query_selector(selector)
        if btn:
            btn.click()
            page.wait_for_timeout(2000)
            break

    input("\n  → Completa el login en el navegador y pulsa Enter aquí: ")
    page.wait_for_timeout(3000)

    if not _is_logged_in(page):
        print("    [APOLLO ERROR] No se detectó el dashboard. Verifica el login.")
        return False

    _save_session(APOLLO_SESSION_PATH, page.context.cookies())
    print("    [APOLLO] Sesión guardada →", APOLLO_SESSION_PATH)
    return True


# ── People search + extraction ────────────────────────────────────────────────

def _search_people_by_domain(page, domain: str) -> None:
    """Navigate to Apollo people search filtered by domain + title keywords."""
    titles_param = "&".join(
        f"personTitles[]={t.replace(' ', '+')}" for t in _DECISION_MAKER_TITLES[:10]
    )
    url = (
        f"{APOLLO_APP_URL}/#/people"
        f"?organizationDomains[]={domain}"
        f"&{titles_param}"
        f"&sortByField=recommendations&sortAscending=false"
    )
    page.goto(url, wait_until="domcontentloaded", timeout=25000)
    page.wait_for_timeout(random.randint(3000, 5000))
    for _ in range(3):
        page.evaluate("window.scrollBy(0, 500)")
        page.wait_for_timeout(random.randint(800, 1500))


def _extract_contacts(page) -> list[dict]:
    """Extract visible name, title, email from Apollo people rows."""
    contacts = []

    # Apollo uses obfuscated CSS classes — try multiple known selectors
    rows = (
        page.query_selector_all("tr.zp_RFed0")
        or page.query_selector_all("[data-cy='people-table-row']")
        or page.query_selector_all("tr[class*='zp_']")
        or page.query_selector_all(".zp_cUMiD")
    )

    for row in rows:
        try:
            name_el = (
                row.query_selector("[data-cy='person-name']")
                or row.query_selector("a[href*='/people/']")
                or row.query_selector(".zp_xvo3G, .zp_Y6y8d a")
            )
            name = name_el.inner_text().strip() if name_el else None

            title_el = (
                row.query_selector("[data-cy='person-title']")
                or row.query_selector("span[class*='title'], .zp_FLD6D, .zp_Y6y8d span")
            )
            title = title_el.inner_text().strip() if title_el else None

            # Email — only collect if not masked
            email = None
            email_el = (
                row.query_selector("a[href^='mailto:']")
                or row.query_selector("[data-cy='person-email'] span")
                or row.query_selector(".zp_DqCa5, .zp_B0ula, span[class*='email']")
            )
            if email_el:
                href = email_el.get_attribute("href") or ""
                if href.startswith("mailto:"):
                    email = href.replace("mailto:", "").strip().lower()
                else:
                    text = email_el.inner_text().strip()
                    if "@" in text and "****" not in text and "Access" not in text:
                        email = text.lower()

            if name:
                contacts.append({"name": name, "title": title or "", "email": email})

        except Exception:
            continue

    return contacts


# ── Batch scraper ─────────────────────────────────────────────────────────────

def run_apollo_scraper(domains: list[str]) -> dict:
    """
    Open one browser session and scrape Apollo people for all domains.
    Updates and returns the apollo_cache.json.

    Args:
        domains: list of domain strings, e.g. ["sinch.com", "pleo.io"]
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  [APOLLO] playwright no instalado — ejecuta: pip install playwright && playwright install chromium")
        return {}

    cache = load_cache()
    new_domains = [d for d in domains if d not in cache]

    if not new_domains:
        print("  [APOLLO] Todos los dominios ya están en caché — nada que scraper")
        return cache

    print(f"  [APOLLO] Scraping {len(new_domains)} dominio(s) nuevos...")

    with sync_playwright() as pw:
        # Use real Chrome (not Playwright's Chromium) so Google OAuth works
        try:
            browser = pw.chromium.launch(channel="chrome", headless=False, slow_mo=40)
        except Exception:
            # Fallback to bundled Chromium if Chrome not installed
            browser = pw.chromium.launch(headless=False, slow_mo=40)
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
        )
        page = context.new_page()

        try:
            if not login_apollo(page):
                print("  [APOLLO] Login fallido — abortando scraper")
                return cache

            for domain in new_domains:
                print(f"\n  [APOLLO] → {domain}")
                try:
                    _search_people_by_domain(page, domain)
                    contacts = _extract_contacts(page)
                    cache[domain] = contacts
                    save_cache(cache)
                    print(f"    {len(contacts)} contacto(s) encontrados")
                    for c in contacts[:5]:
                        print(f"      {c['name']} | {c['title']} | {c['email'] or '(sin email visible)'}")
                    page.wait_for_timeout(random.randint(2000, 4000))
                except Exception as exc:
                    print(f"    [ERROR] {domain}: {exc}")
                    cache[domain] = []
                    save_cache(cache)

            _save_session(APOLLO_SESSION_PATH, context.cookies())

        finally:
            context.close()
            browser.close()

    total_with_email = sum(
        1 for d in new_domains
        for c in cache.get(d, [])
        if c.get("email")
    )
    print(f"\n  [APOLLO] Scraping completado. Emails encontrados: {total_with_email}")
    return cache


# ── CLI entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    from config.settings import PIPELINE_PATH
    from src.utils.pipeline_state import load_state
    from urllib.parse import urlparse

    state = load_state(PIPELINE_PATH)
    companies = state.get("discovered", []) or state.get("enriched", [])

    domains = []
    for c in companies:
        w = c.get("website", "")
        if w:
            d = urlparse(w).netloc.replace("www.", "")
            if d and d not in domains:
                domains.append(d)

    if not domains:
        print("No hay empresas en el pipeline. Ejecuta --step dolmen primero.")
    else:
        print(f"Scraping Apollo para {len(domains)} dominios...")
        run_apollo_scraper(domains)
