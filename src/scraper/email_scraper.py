"""
Stage 2: Find stakeholder emails by scraping company websites.

Strategy:
1. Try subpages most likely to have emails: /contact, /about, /team, /people
2. Scan homepage as fallback
3. Extract emails via regex (with obfuscation decoding)
4. Score by stakeholder priority (CEO/founder > CMO/marketing > generic)
5. Infer stakeholder name from DOM context near the email link
"""

import re
from typing import Optional
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from config.settings import PIPELINE_PATH
from src.utils.http_client import fetch_page, fetch_page_playwright, needs_js_rendering
from src.utils.pipeline_state import load_state, save_state

EMAIL_REGEX = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")

IGNORED_EMAIL_PATTERNS = re.compile(
    r"\.(png|jpg|jpeg|gif|svg|pdf|css|js|woff|ttf)@"
    r"|@sentry\.|@example\.|@domain\.|@test\."
    r"|noreply|no-reply|donotreply|unsubscribe"
    r"|@wixpress\.|@squarespace\.|@shopify\.",
    re.IGNORECASE,
)

# Lower index = higher priority
STAKEHOLDER_PRIORITY = [
    "ceo", "chief executive", "founder", "co-founder", "cofounder",
    "cmo", "chief marketing", "marketing director", "marketing lead",
    "vp marketing", "head of marketing", "vp of marketing",
    "chief growth", "growth lead",
    "marketing@", "growth@", "brand@",
    "hello@", "hi@", "team@",
    "info@", "contact@", "press@",
]

SUBPAGE_CANDIDATES = [
    "/contact",
    "/contact-us",
    "/about",
    "/about-us",
    "/team",
    "/our-team",
    "/people",
    "/who-we-are",
    "/company",
]


def _decode_obfuscation(text: str) -> str:
    """Decode common email obfuscation patterns."""
    return (
        text
        .replace(" [at] ", "@").replace("[at]", "@").replace(" at ", "@")
        .replace(" [dot] ", ".").replace("[dot]", ".").replace("(dot)", ".")
        .replace(" DOT ", ".").replace(" AT ", "@")
    )


def find_emails_in_html(html: str) -> list:
    """Extract all valid email addresses from HTML, filtering noise."""
    cleaned = _decode_obfuscation(html)
    emails = EMAIL_REGEX.findall(cleaned)
    return [
        e.lower() for e in emails
        if not IGNORED_EMAIL_PATTERNS.search(e)
    ]


def score_email(email: str, surrounding_text: str = "") -> int:
    """
    Score email by stakeholder relevance. Lower = higher priority.
    Returns 999 if no known keyword matches.
    """
    combined = (email + " " + surrounding_text).lower()
    for idx, keyword in enumerate(STAKEHOLDER_PRIORITY):
        if keyword in combined:
            return idx
    return 999


def get_surrounding_text(html: str, email: str, window: int = 300) -> str:
    """Extract characters around the email occurrence in raw HTML."""
    pos = html.lower().find(email.lower())
    if pos == -1:
        return ""
    start = max(0, pos - window)
    end = min(len(html), pos + len(email) + window)
    return html[start:end]


def infer_stakeholder_name(html: str, email: str) -> Optional[str]:
    """
    Attempt to find a human name near the email in the DOM.
    Walks up from the mailto link to find a sibling heading or strong tag.
    """
    soup = BeautifulSoup(html, "html.parser")
    for link in soup.find_all("a", href=re.compile(r"mailto:", re.IGNORECASE)):
        if email.lower() in link.get("href", "").lower():
            parent = link.find_parent(["div", "article", "section", "li", "p"])
            if parent:
                for tag in parent.find_all(["h1", "h2", "h3", "h4", "strong", "b"]):
                    text = tag.get_text(strip=True)
                    if 1 <= len(text.split()) <= 5 and "@" not in text and len(text) > 2:
                        return text
    return None


FALLBACK_PATTERNS = ["hello@{}", "info@{}", "contact@{}", "team@{}", "hi@{}"]


def _has_mx_record(domain: str) -> bool:
    """Check if domain has MX records (can receive email)."""
    try:
        import dns.resolver
        dns.resolver.resolve(domain, "MX")
        return True
    except Exception:
        return False


def _guess_email_pattern(website: str) -> dict:
    """Fallback: return a common generic email for the domain after verifying MX."""
    domain = urlparse(website).netloc.replace("www.", "")
    if not domain or not _has_mx_record(domain):
        return {"stakeholder_email": None, "stakeholder_name": None, "email_source_url": None}

    email = FALLBACK_PATTERNS[0].format(domain)
    return {
        "stakeholder_email": email,
        "stakeholder_name": None,
        "email_source_url": "pattern-based",
    }


def find_best_email_for_company(website: str) -> dict:
    """
    Scan subpages of a company website to find the best stakeholder email.

    Returns dict with: stakeholder_email, stakeholder_name, email_source_url
    """
    base = website.rstrip("/")
    candidate_urls = [base] + [base + path for path in SUBPAGE_CANDIDATES]

    all_hits: list = []  # (score, email, source_url, html)

    for url in candidate_urls:
        html = fetch_page(url)
        if html and needs_js_rendering(html):
            html = fetch_page_playwright(url)
        if not html:
            continue

        emails = find_emails_in_html(html)
        for email in set(emails):
            surrounding = get_surrounding_text(html, email)
            s = score_email(email, surrounding)
            all_hits.append((s, email, url, html))

    if not all_hits:
        return _guess_email_pattern(website)

    all_hits.sort(key=lambda x: x[0])
    best_score, best_email, best_url, best_html = all_hits[0]
    name = infer_stakeholder_name(best_html, best_email)

    return {
        "stakeholder_email": best_email,
        "stakeholder_name": name,
        "email_source_url": best_url,
    }


def enrich_companies() -> list:
    """
    Entry point for Stage 2.
    Loads discovered companies from pipeline state, finds stakeholder emails,
    saves enriched records back to pipeline state incrementally.
    """
    state = load_state(PIPELINE_PATH)
    discovered = state.get("discovered", [])
    enriched_websites = {c["website"] for c in state.get("enriched", [])}
    enriched = list(state.get("enriched", []))

    # Skip companies with no website (discovered from LinkedIn with no URL)
    to_process = [c for c in discovered if c.get("website") and c["website"] not in enriched_websites]

    if not to_process:
        print("  No new companies to enrich.")
        return enriched

    for company in to_process:
        print(f"  [ENRICH] {company['company_name']} → {company['website']}")
        result = find_best_email_for_company(company["website"])
        company.update(result)
        enriched.append(company)
        state["enriched"] = enriched
        save_state(PIPELINE_PATH, state)

        status = result["stakeholder_email"] or "NOT FOUND"
        print(f"    → {status}")

    found = sum(1 for c in enriched if c.get("stakeholder_email"))
    print(f"\n  Email coverage: {found}/{len(enriched)} companies ({found * 100 // max(len(enriched), 1)}%)")
    return enriched
