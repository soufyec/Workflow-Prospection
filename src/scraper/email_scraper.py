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

import requests
from bs4 import BeautifulSoup

from config.settings import APOLLO_API_KEY, PIPELINE_PATH
from src.utils.http_client import fetch_page, fetch_page_playwright, needs_js_rendering
from src.utils.pipeline_state import load_state, save_state

EMAIL_REGEX = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")

# Matches international and local phone numbers (8–17 digits with separators)
PHONE_REGEX = re.compile(
    r"(?<!\d)(\+?(?:\d[\s\.\-\(\)]?){7,16}\d)(?!\d)"
)

IGNORED_EMAIL_PATTERNS = re.compile(
    # File extensions appearing before @ (obfuscated filenames)
    r"\.(png|jpg|jpeg|gif|svg|pdf|css|js|woff|woff2|ttf|eot)@"
    # File extensions appearing as TLD (false positives from image src attrs)
    r"|@[^@]+\.(avif|webp|png|jpg|jpeg|gif|svg|ico|woff|ttf|css|js|map|min)$"
    r"|@sentry\.|@example\.|@domain\.|@test\.|@company\."
    r"|noreply|no-reply|donotreply|unsubscribe"
    r"|@wixpress\.|@squarespace\.|@shopify\."
    r"|^you@|^name@|^email@|^user@|^someone@",
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


# TLD suffixes that indicate a ROT13-encoded email (ROT13 of common real TLDs).
# .com→.pbz  .io→.vb  .net→.arg  .org→.bet  .eu→.rh  .nl→.ay  .de→.qr  .co→.pb
_ROT13_TLDS = re.compile(r"\.(pbz|vb|arg|bet|rh|ay|qr|pb|hx|fr|fr)$", re.IGNORECASE)


def _decode_obfuscation(text: str) -> str:
    """Decode common email obfuscation patterns including ROT13."""
    decoded = (
        text
        .replace(" [at] ", "@").replace("[at]", "@").replace(" at ", "@")
        .replace(" [dot] ", ".").replace("[dot]", ".").replace("(dot)", ".")
        .replace(" DOT ", ".").replace(" AT ", "@")
    )
    # ROT13: only attempt on tokens whose TLD looks ROT13-shifted
    for cand in re.findall(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,4}\b", decoded):
        tld_part = "." + cand.rsplit(".", 1)[-1]
        if _ROT13_TLDS.search(tld_part):
            import codecs
            real = codecs.decode(cand, "rot_13")
            if re.match(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,4}$", real):
                decoded = decoded.replace(cand, real)
    return decoded


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


def find_phones_in_html(html: str) -> list:
    """
    Extract phone numbers from HTML. Returns cleaned strings like +31612345678.
    Filters out short sequences that look like years, zip codes or IDs.
    """
    # Strip HTML tags first so we don't match attribute values inside tags
    text = re.sub(r"<[^>]+>", " ", html)
    raw = PHONE_REGEX.findall(text)
    phones = []
    for raw_phone in raw:
        # Remove all separators to count digits
        digits_only = re.sub(r"[^\d]", "", raw_phone)
        # Must have 8–15 digits; skip ZIP codes / years (4 digits)
        if len(digits_only) < 8 or len(digits_only) > 15:
            continue
        # Skip pure-digit sequences that look like years or IDs (no +, spaces, dots, dashes)
        if re.fullmatch(r"\d+", raw_phone.strip()) and len(digits_only) <= 6:
            continue
        # Normalise: strip surrounding whitespace
        clean = raw_phone.strip()
        if clean and clean not in phones:
            phones.append(clean)
    return phones


def _lookup_phone_via_apollo(company_website: str, stakeholder_name: Optional[str]) -> Optional[str]:
    """
    Use Apollo.io People API to find a direct-dial or mobile phone for the
    best stakeholder contact at the given company domain.

    Returns a phone string or None.
    """
    if not APOLLO_API_KEY:
        return None

    domain = urlparse(company_website).netloc.replace("www.", "")
    if not domain:
        return None

    try:
        payload: dict = {
            "q_organization_domains": [domain],
            "person_titles": [
                "CEO", "Founder", "Co-Founder",
                "CMO", "Chief Marketing Officer",
                "Marketing Director", "Head of Marketing",
            ],
            "per_page": 5,
            "page": 1,
        }
        resp = requests.post(
            "https://api.apollo.io/v1/people/search",
            json=payload,
            headers={"Content-Type": "application/json", "X-Api-Key": APOLLO_API_KEY},
            timeout=15,
        )
        if resp.status_code != 200:
            return None
        people = resp.json().get("people") or []
        for person in people:
            phone = person.get("direct_dial_phone_number") or person.get("mobile_phone_number")
            if not phone and person.get("phone_numbers"):
                phone = person["phone_numbers"][0].get("sanitized_number")
            if phone:
                return phone
    except Exception:
        pass
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


def _normalize_url(url: str) -> str:
    """Ensure URL uses https:// and strip www. prefix for consistency."""
    url = url.strip()
    if not url.startswith("http"):
        url = f"https://{url}"
    # Upgrade http → https
    url = re.sub(r"^http://", "https://", url)
    # Strip www. from netloc
    parsed = urlparse(url)
    netloc = re.sub(r"^www\.", "", parsed.netloc)
    return f"https://{netloc}"


def _lookup_website_by_name(company_name: str) -> Optional[str]:
    """
    Search Apollo.io organizations by name to find a company's website.
    Returns the best-matching website URL (https, no www), or None.
    Prefers small organisations (< 200 employees) to avoid matching large corps.
    """
    if not APOLLO_API_KEY:
        return None
    try:
        resp = requests.post(
            "https://api.apollo.io/v1/organizations/search",
            json={"q_organization_name": company_name, "per_page": 5, "page": 1},
            headers={"Content-Type": "application/json", "X-Api-Key": APOLLO_API_KEY},
            timeout=15,
        )
        if resp.status_code != 200:
            return None
        orgs = resp.json().get("organizations") or []
        # Sort candidates by employee count ascending (smallest = most likely startup)
        candidates = []
        for org in orgs:
            name_match = (org.get("name") or "").lower()
            if company_name.lower() not in name_match and name_match not in company_name.lower():
                continue
            website = org.get("website_url") or org.get("primary_domain") or ""
            emp = org.get("estimated_num_employees") or 9999
            if website and emp < 200:
                candidates.append((emp, website))
        if candidates:
            candidates.sort(key=lambda x: x[0])
            return _normalize_url(candidates[0][1])
    except Exception:
        pass
    return None


_INVALID_WEBSITE_RE = re.compile(
    r"\.(pdf|zip|doc|docx|xls|xlsx|ppt|pptx)(\?|$)"
    r"|cdn\.prod\.website-files\.com"
    r"|^(?!https?://)",
    re.IGNORECASE,
)


def find_best_email_for_company(website: str) -> dict:
    """
    Scan subpages of a company website to find the best stakeholder email and phone.

    Returns dict with: stakeholder_email, stakeholder_name, email_source_url, stakeholder_phone
    """
    if not website or _INVALID_WEBSITE_RE.search(website):
        return {"stakeholder_email": None, "stakeholder_name": None, "email_source_url": None, "stakeholder_phone": None}

    # Normalize to https:// to avoid proxy issues with http://
    website = _normalize_url(website)
    base = website.rstrip("/")

    all_hits: list = []  # (score, email, source_url, html)
    all_phones: list = []

    # --- Homepage probe ---
    # If the homepage is completely unreachable (Cloudflare / proxy block),
    # skip all subpages and go straight to the MX fallback — saves ~80 s of
    # Playwright timeouts per company.
    homepage_html = fetch_page(base)
    if homepage_html and needs_js_rendering(homepage_html):
        homepage_html = fetch_page_playwright(base)

    if not homepage_html:
        fallback = _guess_email_pattern(website)
        fallback["stakeholder_phone"] = _lookup_phone_via_apollo(website, None)
        return fallback

    # Homepage is reachable — scan it and then check high-value subpages
    for url in [base] + [base + path for path in SUBPAGE_CANDIDATES]:
        if url == base:
            html = homepage_html  # already fetched
        else:
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

        phones = find_phones_in_html(html)
        all_phones.extend(phones)

    if not all_hits:
        fallback = _guess_email_pattern(website)
        fallback["stakeholder_phone"] = all_phones[0] if all_phones else _lookup_phone_via_apollo(website, None)
        return fallback

    all_hits.sort(key=lambda x: x[0])
    best_score, best_email, best_url, best_html = all_hits[0]
    name = infer_stakeholder_name(best_html, best_email)

    # Phone: prefer website-scraped; fall back to Apollo People API
    phone = all_phones[0] if all_phones else _lookup_phone_via_apollo(website, name)

    return {
        "stakeholder_email": best_email,
        "stakeholder_name": name,
        "email_source_url": best_url,
        "stakeholder_phone": phone,
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

    # For companies without a website, try to find one via Apollo name search
    for company in discovered:
        if not company.get("website"):
            found = _lookup_website_by_name(company["company_name"])
            if found:
                company["website"] = found
                print(f"  [WEBSITE] {company['company_name']} → {found} (via Apollo lookup)")

    # Skip companies with no website after lookup attempt
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

        email_status = result["stakeholder_email"] or "NOT FOUND"
        phone_status = result.get("stakeholder_phone") or "no phone"
        print(f"    → email: {email_status} | phone: {phone_status}")

    found = sum(1 for c in enriched if c.get("stakeholder_email"))
    print(f"\n  Email coverage: {found}/{len(enriched)} companies ({found * 100 // max(len(enriched), 1)}%)")
    return enriched
