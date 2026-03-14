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

def _wait_for_results(page) -> bool:
    """Wait until Apollo has loaded people results (not a spinner/empty state)."""
    for _ in range(20):
        page.wait_for_timeout(1500)
        # Check for any sign of loaded rows or empty state
        has_rows = page.evaluate("""() => {
            const rows = document.querySelectorAll('tr');
            return rows.length > 2;
        }""")
        if has_rows:
            return True
        # Also check for empty state text
        body = page.inner_text("body") or ""
        if "No results" in body or "0 contacts" in body or "no people" in body.lower():
            return False
    return False


def _search_people_by_domain(page, domain: str) -> None:
    """Search Apollo people by domain using the filter UI."""
    # Step 1: navigate to the people page (no filters)
    page.goto(f"{APOLLO_APP_URL}/#/people", wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(3000)

    # Step 2: find the company/domain filter and set it
    # Apollo's filter bar has a "Company" or "Organization" button
    clicked_filter = False
    for selector in [
        "button:has-text('Company')",
        "button:has-text('Organization')",
        "button:has-text('Current Company')",
        "[data-cy='filter-company']",
        "span:has-text('Company')",
    ]:
        el = page.query_selector(selector)
        if el:
            try:
                el.click()
                page.wait_for_timeout(1500)
                clicked_filter = True
                break
            except Exception:
                continue

    if clicked_filter:
        # Type domain in the search input that appeared
        for input_sel in [
            "input[placeholder*='company' i]",
            "input[placeholder*='organization' i]",
            "input[placeholder*='Search' i]",
            "[role='combobox']",
            "input[type='text']",
        ]:
            inp = page.query_selector(input_sel)
            if inp and inp.is_visible():
                try:
                    inp.click()
                    inp.fill(domain)
                    page.wait_for_timeout(2000)
                    # Pick first autocomplete option
                    for opt_sel in [
                        "[role='option']:first-child",
                        "li[role='option']:first-child",
                        ".Select-option:first-child",
                        "[class*='option']:first-child",
                    ]:
                        opt = page.query_selector(opt_sel)
                        if opt and opt.is_visible():
                            opt.click()
                            page.wait_for_timeout(1000)
                            break
                    else:
                        # Press Enter if no autocomplete appeared
                        inp.press("Enter")
                    break
                except Exception:
                    continue

    # Step 3: if UI filter didn't work, fall back to URL with params
    if not clicked_filter:
        from urllib.parse import quote
        titles_param = "&".join(
            f"personTitles[]={quote(t)}" for t in _DECISION_MAKER_TITLES[:8]
        )
        url = (
            f"{APOLLO_APP_URL}/#/people"
            f"?organizationDomains[]={domain}"
            f"&{titles_param}"
            f"&sortByField=recommendations&sortAscending=false"
        )
        page.goto(url, wait_until="domcontentloaded", timeout=25000)

    # Step 4: wait for results to appear
    _wait_for_results(page)

    # Scroll to trigger lazy loading
    for _ in range(4):
        page.evaluate("window.scrollBy(0, 400)")
        page.wait_for_timeout(600)


def _reveal_emails(page) -> None:
    """Click all 'Access email' buttons on the page and wait for emails to load."""
    # Selectors Apollo uses for the email-reveal button
    REVEAL_SELECTORS = [
        "button:has-text('Access email')",
        "button:has-text('Get email')",
        "button:has-text('Reveal email')",
        "button:has-text('Show email')",
        "[data-cy*='access-email']",
        "[class*='accessEmail']",
        "[class*='emailAccess']",
        "span:has-text('Access email')",
    ]

    revealed = 0
    for selector in REVEAL_SELECTORS:
        buttons = page.query_selector_all(selector)
        for btn in buttons:
            try:
                if btn.is_visible():
                    btn.click()
                    page.wait_for_timeout(800)
                    revealed += 1
            except Exception:
                continue
        if revealed:
            break  # one working selector is enough

    if revealed:
        print(f"      → {revealed} email(s) desbloqueado(s)")
        page.wait_for_timeout(1500)  # let all emails render


def _extract_contacts(page) -> list[dict]:
    """Extract visible name, title, email from Apollo people rows using JS."""
    # Use JavaScript to extract data generically — resilient to class changes
    contacts = page.evaluate("""() => {
        const results = [];
        const seen = new Set();

        // Strategy 1: table rows
        const rows = Array.from(document.querySelectorAll('tr')).filter(r => {
            const text = r.innerText || '';
            // Skip header rows and rows with no real content
            return text.length > 10 && !text.startsWith('Name') && !text.startsWith('Title');
        });

        for (const row of rows) {
            // Name: first link to a /people/ profile, or first bold/strong text
            const nameLink = row.querySelector('a[href*="/people/"]');
            const name = nameLink ? nameLink.innerText.trim() : null;
            if (!name || seen.has(name)) continue;

            // Title: second or third td text
            const cells = Array.from(row.querySelectorAll('td'));
            let title = null;
            if (cells.length > 1) {
                // Title is usually in the 2nd or 3rd cell
                for (let i = 1; i < Math.min(cells.length, 4); i++) {
                    const t = cells[i].innerText.trim();
                    if (t && t.length > 2 && !t.includes('@') && !/^[+\\d]/.test(t)) {
                        title = t;
                        break;
                    }
                }
            }

            // Email: mailto link or text containing @
            let email = null;
            const mailtoLink = row.querySelector('a[href^="mailto:"]');
            if (mailtoLink) {
                email = mailtoLink.href.replace('mailto:', '').trim().toLowerCase();
            } else {
                // Look for any span/td containing a valid email
                for (const el of row.querySelectorAll('span, td, div')) {
                    const t = el.innerText.trim();
                    if (t.includes('@') && t.includes('.') && !t.includes('****')
                        && !t.includes('Access') && !t.includes(' ') && t.length < 80) {
                        email = t.toLowerCase();
                        break;
                    }
                }
            }

            seen.add(name);
            results.push({ name, title: title || '', email });
        }

        // Strategy 2: card/list views (non-table layout)
        if (results.length === 0) {
            const cards = document.querySelectorAll('[class*="person"], [class*="contact"], [class*="people"]');
            for (const card of cards) {
                const links = card.querySelectorAll('a[href*="/people/"]');
                const name = links.length ? links[0].innerText.trim() : null;
                if (!name || seen.has(name)) continue;
                const text = card.innerText || '';
                const emailMatch = text.match(/[a-z0-9._%+\\-]+@[a-z0-9.\\-]+\\.[a-z]{2,}/i);
                seen.add(name);
                results.push({ name, title: '', email: emailMatch ? emailMatch[0].toLowerCase() : null });
            }
        }

        return results;
    }""")

    # Filter: only keep entries with a real name (length > 1)
    return [c for c in (contacts or []) if c.get("name") and len(c["name"]) > 1]


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
    # Re-scrape domains that are missing OR were cached as empty (broken prior run)
    new_domains = [d for d in domains if d not in cache or cache[d] == []]

    if not new_domains:
        print("  [APOLLO] Todos los dominios ya están en caché — nada que scraper")
        return cache

    print(f"  [APOLLO] Scraping {len(new_domains)} dominio(s) nuevos...")

    with sync_playwright() as pw:
        # Use the user's real Chrome profile so Google OAuth works
        # (Google blocks any browser launched fresh by Playwright)
        import platform, tempfile
        system = platform.system()
        if system == "Windows":
            chrome_profile = os.path.join(
                os.environ.get("LOCALAPPDATA", ""),
                "Google", "Chrome", "User Data"
            )
        elif system == "Darwin":
            chrome_profile = os.path.expanduser(
                "~/Library/Application Support/Google/Chrome"
            )
        else:
            chrome_profile = os.path.expanduser("~/.config/google-chrome")

        # Use a copy of the profile dir to avoid Chrome "profile in use" lock
        tmp_profile = os.path.join(tempfile.gettempdir(), "apollo_chrome_profile")

        context = None
        try:
            context = pw.chromium.launch_persistent_context(
                user_data_dir=tmp_profile,
                channel="chrome",
                headless=False,
                slow_mo=40,
                viewport={"width": 1280, "height": 800},
                args=["--disable-blink-features=AutomationControlled"],
                ignore_default_args=["--enable-automation"],
            )
        except Exception as e:
            print(f"  [APOLLO] No se pudo usar Chrome real ({e}), usando Chromium...")
            context = pw.chromium.launch_persistent_context(
                user_data_dir=tmp_profile,
                headless=False,
                slow_mo=40,
                viewport={"width": 1280, "height": 800},
                args=["--disable-blink-features=AutomationControlled"],
                ignore_default_args=["--enable-automation"],
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
                    _reveal_emails(page)
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
