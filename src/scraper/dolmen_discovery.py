"""
Stage 1 (Dõlmen mode): Discover companies by day-of-week geographic rotation
via Apollo.io, targeting sectors and employee size relevant to Dõlmen Studios.

Geographic rotation:
  Monday    → London 🇬🇧
  Tuesday   → Berlin / Munich 🇩🇪
  Wednesday → Amsterdam 🇳🇱
  Thursday  → Stockholm / Copenhagen 🇸🇪🇩🇰
  Friday    → Madrid / Barcelona 🇪🇸
  (Weekend  → fallback to Mon/Fri)

Target: 10–150 employees, budget signals, brand/web clearly neglected.
Sectors: SaaS, FinTech, eCommerce, Architecture, Hospitality, Consulting, Industrial.
"""

import random
import time
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse

import requests

from config.settings import APOLLO_API_KEY

APOLLO_BASE_URL = "https://api.apollo.io/v1"

# Day-of-week → (display label, Apollo organization_locations list)
GEO_ROTATION = {
    0: ("London 🇬🇧",               ["London, England, United Kingdom"]),
    1: ("Berlin / Munich 🇩🇪",       ["Berlin, Berlin, Germany", "Munich, Bavaria, Germany"]),
    2: ("Amsterdam 🇳🇱",             ["Amsterdam, North Holland, Netherlands"]),
    3: ("Stockholm / Copenhagen 🇸🇪🇩🇰", ["Stockholm, Stockholm County, Sweden", "Copenhagen, Capital Region, Denmark"]),
    4: ("Madrid / Barcelona 🇪🇸",    ["Madrid, Community of Madrid, Spain", "Barcelona, Catalonia, Spain"]),
    5: ("London 🇬🇧",               ["London, England, United Kingdom"]),   # Saturday
    6: ("Madrid / Barcelona 🇪🇸",    ["Madrid, Community of Madrid, Spain", "Barcelona, Catalonia, Spain"]),  # Sunday
}

GEO_ALIASES = {
    "london":    0,
    "berlin":    1,
    "munich":    1,
    "amsterdam": 2,
    "stockholm": 3,
    "copenhagen":3,
    "madrid":    4,
    "barcelona": 4,
}

# Decision-maker titles to look up via Apollo People API
DECISION_MAKER_TITLES = [
    "CEO", "Founder", "Co-Founder",
    "CMO", "Chief Executive Officer", "Chief Marketing Officer",
    "Managing Director", "Director",
]

MAX_ORG_PAGES = 3
PER_PAGE = 25
# Maximum companies to enrich with Apollo People API (to conserve credits)
MAX_PEOPLE_LOOKUPS = 30


def get_today_geo() -> tuple:
    """Return (label, locations_list) for today's weekday."""
    return GEO_ROTATION[datetime.now().weekday()]


def _apollo_headers() -> dict:
    return {
        "Content-Type": "application/json",
        "Cache-Control": "no-cache",
        "X-Api-Key": APOLLO_API_KEY,
    }


def _search_orgs_page(locations: list, page: int) -> Optional[dict]:
    payload = {
        "organization_locations": locations,
        "num_employees_ranges": ["10,150"],
        "per_page": PER_PAGE,
        "page": page,
    }
    try:
        resp = requests.post(
            f"{APOLLO_BASE_URL}/organizations/search",
            json=payload,
            headers=_apollo_headers(),
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else "?"
        print(f"  [DOLMEN] Apollo orgs HTTP {status}: {e}")
        return None
    except requests.RequestException as e:
        print(f"  [DOLMEN] Apollo orgs error: {e}")
        return None


def _search_decision_maker(domain: str) -> Optional[dict]:
    """Search Apollo People API for the top Founder/CEO/CMO at a given domain."""
    payload = {
        "q_organization_domains": [domain],
        "person_titles": DECISION_MAKER_TITLES,
        "per_page": 5,
        "page": 1,
    }
    try:
        resp = requests.post(
            f"{APOLLO_BASE_URL}/people/search",
            json=payload,
            headers=_apollo_headers(),
            timeout=15,
        )
        if resp.status_code != 200:
            return None
        people = resp.json().get("people") or []
        if not people:
            return None
        # Prefer Founder > CEO > CMO
        priority = ["founder", "co-founder", "ceo", "chief executive", "cmo", "chief marketing", "managing director"]
        for target in priority:
            for person in people:
                if target in (person.get("title") or "").lower():
                    return person
        return people[0]
    except Exception:
        return None


def discover_dolmen_leads(force_city: Optional[str] = None) -> list:
    """
    Discover companies for today's geographic market using Apollo.io.

    Args:
        force_city: Override today's geo. Options: london, berlin, amsterdam,
                    stockholm, copenhagen, madrid, barcelona.
    Returns:
        List of pipeline-compatible company dicts.
    """
    if not APOLLO_API_KEY:
        print("  [DOLMEN] APOLLO_API_KEY not set — skipping discovery.")
        return []

    if force_city:
        day_idx = GEO_ALIASES.get(force_city.lower())
        geo_label, locations = GEO_ROTATION[day_idx] if day_idx is not None else get_today_geo()
    else:
        geo_label, locations = get_today_geo()

    print(f"  [DOLMEN] Market of the day: {geo_label}")
    print(f"  [DOLMEN] Searching Apollo — 10–150 employees, locations: {', '.join(locations)}")

    all_companies = []
    seen_domains: set = set()

    for page in range(1, MAX_ORG_PAGES + 1):
        print(f"    → Orgs page {page}/{MAX_ORG_PAGES}...")
        data = _search_orgs_page(locations, page)
        if not data:
            break

        organizations = data.get("organizations") or data.get("accounts") or []
        if not organizations:
            print("    → No more results.")
            break

        for org in organizations:
            website = (org.get("website_url") or org.get("primary_domain") or "").strip()
            if not website:
                continue
            if not website.startswith("http"):
                website = f"https://{website}"

            domain = urlparse(website).netloc.replace("www.", "")
            if not domain or domain in seen_domains:
                continue
            seen_domains.add(domain)

            keywords = org.get("keywords") or org.get("industry_tag_names") or []
            industry = ", ".join(str(k) for k in keywords[:3]) if keywords else (org.get("industry") or "")
            name = (
                org.get("name")
                or org.get("organization_name")
                or domain.split(".")[0].capitalize()
            )

            all_companies.append({
                "company_name": name,
                "website": f"https://{domain}",
                "country": org.get("country") or "",
                "city": org.get("city") or "",
                "geo_label": geo_label,
                "industry": industry,
                "employee_count": org.get("estimated_num_employees") or 0,
                "vc_source": "",
                "discovery_source": "dolmen_geo",
                "post_text": "",
                "company_linkedin": org.get("linkedin_url") or "",
                "stakeholder_email": None,
                "stakeholder_name": None,
                "stakeholder_role": None,
                "stakeholder_phone": None,
                "linkedin_url": "",
                "email_source_url": None,
                "email_subject": None,
                "email_body": None,
                "review_status": "pending",
                "checklist_score": None,
                "brand_problem": None,
            })

        pagination = data.get("pagination", {})
        total = pagination.get("total_entries", 0)
        print(f"    → Got {len(organizations)} orgs (total available: {total})")
        if page * PER_PAGE >= total:
            break
        time.sleep(random.uniform(1.5, 3.0))

    print(f"  [DOLMEN] {len(all_companies)} unique companies found in {geo_label}")

    # Enrich top N with decision-maker info from Apollo People API
    lookup_count = min(len(all_companies), MAX_PEOPLE_LOOKUPS)
    if lookup_count:
        print(f"  [DOLMEN] Looking up decision makers for top {lookup_count} companies...")
    for company in all_companies[:lookup_count]:
        domain = urlparse(company["website"]).netloc.replace("www.", "")
        person = _search_decision_maker(domain)
        if person:
            company["stakeholder_name"] = person.get("name") or ""
            company["stakeholder_role"] = person.get("title") or ""
            company["linkedin_url"] = person.get("linkedin_url") or ""
            # Apollo sometimes returns email directly
            if person.get("email"):
                company["stakeholder_email"] = person["email"]
        time.sleep(random.uniform(0.5, 1.5))

    print(f"  [DOLMEN] Discovery complete: {len(all_companies)} companies")
    return all_companies
