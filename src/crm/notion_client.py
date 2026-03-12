"""
Notion CRM Integration — push qualified leads to the Dõlmen Studios prospection database.

Notion database: https://www.notion.so/a6b9c06069c244b797572b71fc666bed

Expected database properties (create them in Notion if missing):
  Company         — Title
  Website         — URL
  Country         — Text
  Sector          — Text
  Decision Maker  — Text
  Role            — Text
  LinkedIn        — URL
  Email           — Text (Email)
  Best Channel    — Text
  Status          — Select  (option: "🆕 New")
  Assigned To     — Text
  Checklist Score — Number
  Brand Problem   — Text
  Outreach Email  — Text
"""

import re

import requests

from config.settings import NOTION_API_KEY, NOTION_DATABASE_ID, SENDER_NAME

NOTION_PAGES_URL = "https://api.notion.com/v1/pages"
NOTION_VERSION = "2022-06-28"
NOTION_QUERY_URL = "https://api.notion.com/v1/databases/{db_id}/query"


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Content-Type": "application/json",
        "Notion-Version": NOTION_VERSION,
    }


# ── Property builders ─────────────────────────────────────────────────────────

def _title(value: str) -> dict:
    return {"title": [{"text": {"content": str(value or "")[:2000]}}]}


def _rich_text(value: str) -> dict:
    return {"rich_text": [{"text": {"content": str(value or "")[:2000]}}]}


def _url(value: str) -> dict:
    v = str(value or "").strip()
    # Notion URL fields reject empty strings — must be None or a valid URL
    if not v or not v.startswith("http"):
        return {"url": None}
    return {"url": v}


def _select(value: str) -> dict:
    return {"select": {"name": str(value or "")[:100]}}


def _number(value) -> dict:
    try:
        return {"number": int(value)}
    except (TypeError, ValueError):
        return {"number": None}


def _strip_html(html: str) -> str:
    """Convert HTML email body to plain text for Notion."""
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:2000]


# ── Best channel heuristic ────────────────────────────────────────────────────

def _best_channel(company: dict) -> str:
    if company.get("stakeholder_email"):
        return "Email"
    if company.get("linkedin_url") or company.get("stakeholder_linkedin"):
        return "LinkedIn DM"
    return "Contact Form"


# ── Main push function ────────────────────────────────────────────────────────

def push_lead_to_notion(company: dict) -> bool:
    """
    Create one page (row) in the Notion CRM database for a lead.
    Returns True on success.
    """
    if not NOTION_API_KEY or not NOTION_DATABASE_ID:
        return False

    sector = str(company.get("industry") or "")[:200]
    email_body_raw = company.get("email_body") or ""
    email_plain = _strip_html(email_body_raw) if email_body_raw else ""

    linkedin = (
        company.get("linkedin_url")
        or company.get("stakeholder_linkedin")
        or ""
    )

    properties = {
        "Company":        _title(company.get("company_name", "")),
        "Website":        _url(company.get("website", "")),
        "Country":        _rich_text(company.get("country", "")),
        "Sector":         _rich_text(sector),
        "Decision Maker": _rich_text(company.get("stakeholder_name", "")),
        "Role":           _rich_text(company.get("stakeholder_role", "")),
        "LinkedIn":       _url(linkedin),
        "Email":          _rich_text(company.get("stakeholder_email", "")),
        "Best Channel":   _rich_text(_best_channel(company)),
        "Status":         _select("🆕 New"),
        "Assigned To":    _rich_text(SENDER_NAME),
        "Checklist Score": _number(company.get("checklist_score")),
        "Brand Problem":  _rich_text(company.get("brand_problem", "")),
        "Outreach Email": _rich_text(email_plain),
    }

    payload = {
        "parent": {"database_id": NOTION_DATABASE_ID},
        "properties": properties,
    }

    try:
        resp = requests.post(
            NOTION_PAGES_URL,
            json=payload,
            headers=_headers(),
            timeout=15,
        )
        resp.raise_for_status()
        print(f"  [NOTION] ✓ {company.get('company_name')}")
        return True
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else "?"
        body = (e.response.text or "")[:300] if e.response is not None else ""
        print(f"  [NOTION] ✗ {company.get('company_name')} — HTTP {status}: {body}")
        return False
    except requests.RequestException as e:
        print(f"  [NOTION] ✗ Request error: {e}")
        return False


def find_lead_page_id(company_name: str) -> str | None:
    """
    Search the Notion database for a page matching company_name (Title field).
    Returns the page_id string, or None if not found.
    """
    if not NOTION_API_KEY or not NOTION_DATABASE_ID:
        return None

    url = NOTION_QUERY_URL.format(db_id=NOTION_DATABASE_ID)
    payload = {
        "filter": {
            "property": "Company",
            "title": {"equals": company_name},
        },
        "page_size": 1,
    }
    try:
        resp = requests.post(url, json=payload, headers=_headers(), timeout=15)
        resp.raise_for_status()
        results = resp.json().get("results", [])
        if results:
            return results[0]["id"]
    except requests.RequestException as e:
        print(f"  [NOTION] ✗ find failed for '{company_name}': {e}")
    return None


def update_lead_status(company_name: str, status: str = "📤 Contacted") -> bool:
    """
    Update the Status of an existing Notion page to `status`.
    Returns True on success.
    """
    page_id = find_lead_page_id(company_name)
    if not page_id:
        print(f"  [NOTION] ✗ '{company_name}' not found in CRM — skipping status update")
        return False

    url = f"{NOTION_PAGES_URL}/{page_id}"
    payload = {"properties": {"Status": _select(status)}}
    try:
        resp = requests.patch(url, json=payload, headers=_headers(), timeout=15)
        resp.raise_for_status()
        print(f"  [NOTION] ✓ '{company_name}' → {status}")
        return True
    except requests.RequestException as e:
        print(f"  [NOTION] ✗ update failed for '{company_name}': {e}")
        return False


def push_leads_to_notion(companies: list) -> int:
    """
    Push a list of company dicts to Notion CRM.
    Returns count of successful inserts.
    """
    if not NOTION_API_KEY or not NOTION_DATABASE_ID:
        print("  [NOTION] Skipped — NOTION_API_KEY / NOTION_DATABASE_ID not configured.")
        print("  → Add them to your .env to enable Notion CRM sync.")
        return 0

    pushed = 0
    for company in companies:
        if push_lead_to_notion(company):
            pushed += 1

    print(f"\n  [NOTION] {pushed}/{len(companies)} leads pushed to CRM")
    return pushed
