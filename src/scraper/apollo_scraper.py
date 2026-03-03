"""
Stage 1d: Discover pre-seed and seed startups via Apollo.io API.

Searches Apollo.io's company database for recently funded startups across Europe.
Filters by funding stage (pre-seed, seed) to surface early-stage prospects for
Dõlmen Studios.

Setup:
  1. Get your Apollo.io API key from https://developer.apollo.io/
  2. Add APOLLO_API_KEY=your_key to your .env file

Apollo API docs: https://apolloio.github.io/apollo-api-docs/
"""

import random
import time
from typing import Optional
from urllib.parse import urlparse

import requests

from config.settings import APOLLO_API_KEY

APOLLO_BASE_URL = "https://api.apollo.io/v1"
APOLLO_MIXED_COMPANIES_URL = f"{APOLLO_BASE_URL}/mixed_companies/search"

# Apollo funding stage identifiers (as used in Apollo's API)
TARGET_FUNDING_STAGES = ["pre_seed", "seed"]

# European geographies to focus on
TARGET_LOCATIONS = [
    "Netherlands",
    "Belgium",
    "Germany",
    "France",
    "Spain",
    "Sweden",
    "Denmark",
    "Finland",
    "Norway",
    "United Kingdom",
    "Ireland",
    "Austria",
    "Switzerland",
    "Portugal",
    "Poland",
]

MAX_PAGES = 4
PER_PAGE = 25

_FUNDING_STAGE_MAP = {
    "pre_seed": "pre-seed",
    "seed": "seed",
    "series_a": "Series A",
    "series_b": "Series B",
    "series_c": "Series C",
    "venture": "seed",
    "angel": "pre-seed",
}


def _map_funding_stage(value: str) -> str:
    return _FUNDING_STAGE_MAP.get(value.lower().replace(" ", "_"), value)


def _fetch_apollo_page(page: int) -> Optional[dict]:
    """Call Apollo.io mixed_companies/search and return the parsed JSON response."""
    headers = {
        "Content-Type": "application/json",
        "Cache-Control": "no-cache",
    }
    payload = {
        "api_key": APOLLO_API_KEY,
        "funding_stage": TARGET_FUNDING_STAGES,
        "organization_locations": TARGET_LOCATIONS,
        "per_page": PER_PAGE,
        "page": page,
    }
    try:
        resp = requests.post(
            APOLLO_MIXED_COMPANIES_URL,
            json=payload,
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else "?"
        if status == 401:
            print("  [APOLLO] 401 Unauthorized — check your APOLLO_API_KEY in .env")
        elif status == 422:
            print("  [APOLLO] 422 Unprocessable — check search parameters")
        else:
            print(f"  [APOLLO] HTTP {status} error: {e}")
        return None
    except requests.RequestException as e:
        print(f"  [APOLLO] Request error on page {page}: {e}")
        return None


def discover_from_apollo() -> list:
    """
    Search Apollo.io for pre-seed and seed startups in Europe.
    Returns a list of company dicts compatible with the pipeline format.
    """
    if not APOLLO_API_KEY:
        print("  [APOLLO] APOLLO_API_KEY not configured — skipping Apollo discovery.")
        print("  → Add APOLLO_API_KEY=your_key to your .env file to enable this source.")
        return []

    print("  [APOLLO] Searching pre-seed & seed startups via Apollo.io…")
    all_companies = []
    seen_domains: set = set()

    for page in range(1, MAX_PAGES + 1):
        print(f"    → Page {page}/{MAX_PAGES}…")
        data = _fetch_apollo_page(page)
        if not data:
            break

        organizations = data.get("organizations") or data.get("accounts") or []
        if not organizations:
            print("    → No more results from Apollo.")
            break

        for org in organizations:
            # Website / domain resolution
            website = (
                org.get("website_url")
                or org.get("primary_domain")
                or ""
            ).strip()
            if not website:
                continue
            if not website.startswith("http"):
                website = f"https://{website}"

            domain = urlparse(website).netloc.replace("www.", "")
            if not domain or domain in seen_domains:
                continue
            seen_domains.add(domain)

            # Funding stage
            raw_stage = (
                org.get("latest_funding_stage")
                or org.get("funding_stage")
                or "seed"
            )
            funding_stage = _map_funding_stage(str(raw_stage))

            # Industry — Apollo returns keyword tags
            keywords = org.get("keywords") or org.get("industry_tag_names") or []
            industry = ", ".join(str(k) for k in keywords[:3]) if keywords else "tech"

            # Company name
            name = org.get("name") or org.get("organization_name") or domain.split(".")[0].capitalize()

            all_companies.append({
                "company_name": name,
                "website": f"https://{domain}",
                "vc_source": "Apollo.io",
                "funding_stage": funding_stage,
                "industry": industry,
                "discovery_source": "apollo",
                "post_text": "",
                "stakeholder_email": None,
                "stakeholder_name": None,
                "email_source_url": None,
                "email_subject": None,
                "email_body": None,
                "review_status": "pending",
            })

        pagination = data.get("pagination", {})
        total = pagination.get("total_entries", 0)
        print(f"    → Got {len(organizations)} companies (total available: {total})")

        if page * PER_PAGE >= total:
            break

        time.sleep(random.uniform(1.5, 3.0))

    print(f"  [APOLLO] Unique companies found: {len(all_companies)}")
    return all_companies
