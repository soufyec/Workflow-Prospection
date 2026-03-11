"""
Multi-provider email enrichment waterfall.

Providers tried in order (domain-based first, then name-based, then LinkedIn):

  Domain-based (no name required):
    1. Prospeo      — domain search → returns multiple ranked contacts
    2. FindyMail    — domain search (if plan supports it)

  Name-based (stakeholder name required):
    3. FindyMail    — find by name + domain
    4. IcyPeas      — email search by name + domain
    5. LeadMagic    — email finder by name + domain

  LinkedIn-based (linkedin_url required):
    6. Wiza         — LinkedIn URL → email
    7. ContactOut   — LinkedIn URL → email

  Verification (any email):
    8. FindyMail    — verify any candidate email

Usage:
  from src.enricher.multi_provider import enrich_email, verify_email

  result = enrich_email(company_dict)
  # Returns:
  # {
  #   "stakeholder_email": str | None,
  #   "stakeholder_name": str | None,
  #   "stakeholder_title": str | None,
  #   "email_confidence": float | None,  # 0.0 – 1.0
  #   "email_verified": bool,
  #   "email_provider": str | None,
  # }
"""

from __future__ import annotations

import logging
import re
from typing import Optional
from urllib.parse import urlparse

import requests

from config.settings import (
    CONTACTOUT_API_KEY,
    FINDYMAIL_API_KEY,
    ICYPEAS_API_KEY,
    LEADMAGIC_API_KEY,
    PROSPEO_API_KEY,
    WIZA_API_KEY,
)

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Title priority (lower index = higher priority), mirrors email_scraper.py
# ──────────────────────────────────────────────────────────────────────────────
_TITLE_PRIORITY: list[str] = [
    "ceo", "chief executive",
    "founder", "co-founder", "cofounder",
    "cmo", "chief marketing",
    "marketing director", "marketing lead", "head of marketing",
    "vp marketing", "vp of marketing",
    "chief growth", "growth lead", "growth director",
    "coo", "chief operating",
    "managing director", "general manager",
]

_GENERIC_PREFIXES = re.compile(
    r"^(info|contact|hello|hi|team|sales|support|office|hallo|bonjour)@",
    re.IGNORECASE,
)


def _score_title(title: str) -> int:
    t = title.lower()
    for idx, kw in enumerate(_TITLE_PRIORITY):
        if kw in t:
            return idx
    return 999


def _domain_from(website: str) -> str:
    parsed = urlparse(website if "://" in website else f"https://{website}")
    return parsed.netloc.replace("www.", "") or website.replace("www.", "")


def _get(url: str, headers: dict, params: dict | None = None, timeout: int = 12) -> dict:
    try:
        r = requests.get(url, headers=headers, params=params, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        logger.debug("GET %s failed: %s", url, exc)
        return {}


def _post(url: str, headers: dict, body: dict, timeout: int = 12) -> dict:
    try:
        r = requests.post(url, json=body, headers=headers, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        logger.debug("POST %s failed: %s", url, exc)
        return {}


# ──────────────────────────────────────────────────────────────────────────────
# Candidate dataclass (plain dict for simplicity)
# ──────────────────────────────────────────────────────────────────────────────

def _candidate(email: str, name: str | None, title: str | None,
               confidence: float, provider: str) -> dict:
    return {
        "email": email.lower().strip(),
        "name": name,
        "title": title,
        "confidence": confidence,
        "provider": provider,
        "title_score": _score_title(title or ""),
    }


# ──────────────────────────────────────────────────────────────────────────────
# Provider 1 — Prospeo (domain search)
# ──────────────────────────────────────────────────────────────────────────────

def _prospeo_domain_search(domain: str, company_name: str) -> list[dict]:
    """POST /domain-search → list of contacts ranked by title."""
    if not PROSPEO_API_KEY:
        return []
    body = {
        "url": domain,
        "limit": 10,
    }
    if company_name:
        body["company"] = company_name
    data = _post(
        "https://api.prospeo.io/domain-search",
        headers={"X-KEY": PROSPEO_API_KEY, "Content-Type": "application/json"},
        body=body,
    )
    contacts = data.get("response", {}).get("contacts") or data.get("contacts") or []
    results = []
    for c in contacts:
        email_obj = c.get("email") or {}
        email = (
            email_obj.get("value")
            if isinstance(email_obj, dict)
            else email_obj
        ) or c.get("email_address") or ""
        if not email or "@" not in email:
            continue
        confidence_raw = email_obj.get("confidence", 0) if isinstance(email_obj, dict) else 0
        results.append(_candidate(
            email=email,
            name=f"{c.get('first_name', '')} {c.get('last_name', '')}".strip() or None,
            title=c.get("job_title") or c.get("title"),
            confidence=min(float(confidence_raw) / 100, 1.0) if confidence_raw else 0.7,
            provider="prospeo",
        ))
    return results


def _prospeo_email_finder(first: str, last: str, domain: str) -> list[dict]:
    """POST /email-finder → single contact by name."""
    if not PROSPEO_API_KEY or not first or not last:
        return []
    data = _post(
        "https://api.prospeo.io/email-finder",
        headers={"X-KEY": PROSPEO_API_KEY, "Content-Type": "application/json"},
        body={"first_name": first, "last_name": last, "company": domain},
    )
    resp = data.get("response", {})
    email_obj = resp.get("email") or {}
    email = (
        email_obj.get("value")
        if isinstance(email_obj, dict)
        else email_obj
    ) or ""
    if not email or "@" not in email:
        return []
    confidence_raw = email_obj.get("confidence", 0) if isinstance(email_obj, dict) else 0
    return [_candidate(
        email=email,
        name=f"{first} {last}",
        title=resp.get("job_title"),
        confidence=min(float(confidence_raw) / 100, 1.0) if confidence_raw else 0.7,
        provider="prospeo",
    )]


# ──────────────────────────────────────────────────────────────────────────────
# Provider 2 — FindyMail (find by name + domain, verify)
# ──────────────────────────────────────────────────────────────────────────────

def _findymail_find(name: str, domain: str) -> list[dict]:
    """POST /api/find → single email by full name + domain."""
    if not FINDYMAIL_API_KEY or not name or not domain:
        return []
    data = _post(
        "https://app.findymail.com/api/find",
        headers={
            "Authorization": f"Bearer {FINDYMAIL_API_KEY}",
            "Content-Type": "application/json",
        },
        body={"name": name, "domain": domain},
    )
    email = data.get("email")
    if not email or "@" not in email:
        return []
    validation = data.get("validation") or {}
    status = validation.get("status", "")
    confidence = 0.95 if status == "valid" else 0.6
    return [_candidate(
        email=email,
        name=name,
        title=None,
        confidence=confidence,
        provider="findymail",
    )]


def verify_email(email: str) -> tuple[bool, float]:
    """
    Verify an email with FindyMail.

    Returns (is_valid, confidence_score).
    Falls back to (True, 0.5) if API key missing or call fails.
    """
    if not FINDYMAIL_API_KEY or not email:
        return True, 0.5
    data = _post(
        "https://app.findymail.com/api/verify",
        headers={
            "Authorization": f"Bearer {FINDYMAIL_API_KEY}",
            "Content-Type": "application/json",
        },
        body={"email": email},
    )
    validation = data.get("validation") or {}
    status = validation.get("status", "")
    if status == "valid":
        return True, 0.97
    if status == "invalid":
        return False, 0.0
    if status in ("risky", "unknown"):
        return True, 0.5
    # No key or call failed — assume OK
    return True, 0.5


# ──────────────────────────────────────────────────────────────────────────────
# Provider 3 — IcyPeas (email search by name + domain)
# ──────────────────────────────────────────────────────────────────────────────

def _icypeas_search(first: str, last: str, domain: str) -> list[dict]:
    """POST /api/email-search → email by name + domain."""
    if not ICYPEAS_API_KEY or not first or not last:
        return []
    data = _post(
        "https://api.icypeas.com/api/email-search",
        headers={
            "Authorization": ICYPEAS_API_KEY,
            "Content-Type": "application/json",
        },
        body={"firstname": first, "lastname": last, "domainOrCompany": domain},
    )
    item = data.get("item") or {}
    emails = item.get("emails") or []
    results = []
    for e in emails:
        val = e.get("value") or e if isinstance(e, str) else ""
        if not val or "@" not in val:
            continue
        score = e.get("score", 70) if isinstance(e, dict) else 70
        results.append(_candidate(
            email=val,
            name=f"{first} {last}",
            title=None,
            confidence=min(float(score) / 100, 1.0),
            provider="icypeas",
        ))
    return results


# ──────────────────────────────────────────────────────────────────────────────
# Provider 4 — LeadMagic (email finder by name + domain)
# ──────────────────────────────────────────────────────────────────────────────

def _leadmagic_find(first: str, last: str, company_name: str, domain: str) -> list[dict]:
    """POST /email-finder → single email by name + company domain."""
    if not LEADMAGIC_API_KEY or not first or not last:
        return []
    data = _post(
        "https://api.leadmagic.io/email-finder",
        headers={
            "X-BLOBR-KEY": LEADMAGIC_API_KEY,
            "Content-Type": "application/json",
        },
        body={
            "first_name": first,
            "last_name": last,
            "company_name": company_name or domain,
            "company_domain": domain,
        },
    )
    email = data.get("work_email") or data.get("email") or ""
    if not email or "@" not in email:
        return []
    return [_candidate(
        email=email,
        name=f"{first} {last}",
        title=data.get("title") or data.get("job_title"),
        confidence=0.80,
        provider="leadmagic",
    )]


# ──────────────────────────────────────────────────────────────────────────────
# Provider 5 — Wiza (LinkedIn URL → email)
# ──────────────────────────────────────────────────────────────────────────────

def _wiza_linkedin(linkedin_url: str) -> list[dict]:
    """POST /api/profiles → email from LinkedIn profile URL."""
    if not WIZA_API_KEY or not linkedin_url:
        return []
    data = _post(
        "https://wiza.co/api/profiles",
        headers={
            "Authorization": f"Bearer {WIZA_API_KEY}",
            "Content-Type": "application/json",
        },
        body={"url": linkedin_url},
    )
    profile = data.get("data", {}) or data.get("profile", {}) or {}
    email = profile.get("email") or profile.get("work_email") or ""
    if not email or "@" not in email:
        return []
    return [_candidate(
        email=email,
        name=f"{profile.get('first_name', '')} {profile.get('last_name', '')}".strip() or None,
        title=profile.get("title") or profile.get("job_title"),
        confidence=0.85,
        provider="wiza",
    )]


# ──────────────────────────────────────────────────────────────────────────────
# Provider 6 — ContactOut (LinkedIn URL → email)
# ──────────────────────────────────────────────────────────────────────────────

def _contactout_linkedin(linkedin_url: str) -> list[dict]:
    """GET /v1/people/search?linkedin_url=... → email from LinkedIn."""
    if not CONTACTOUT_API_KEY or not linkedin_url:
        return []
    data = _get(
        "https://api.contactout.com/v1/people/search",
        headers={"Authorization": f"Token {CONTACTOUT_API_KEY}"},
        params={"linkedin_url": linkedin_url},
    )
    profile = data.get("profile") or {}
    emails = profile.get("email") or []
    if isinstance(emails, str):
        emails = [emails]
    results = []
    for e in emails:
        if e and "@" in e:
            results.append(_candidate(
                email=e,
                name=profile.get("full_name"),
                title=profile.get("title"),
                confidence=0.85,
                provider="contactout",
            ))
    return results


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _split_name(full_name: str) -> tuple[str, str]:
    """Split 'John Doe' → ('John', 'Doe'). Returns ('', '') if unclear."""
    parts = full_name.strip().split()
    if len(parts) >= 2:
        return parts[0], " ".join(parts[1:])
    return "", ""


def _pick_best(candidates: list[dict]) -> dict | None:
    """
    Pick the single best email candidate.
    Priority: title_score ASC → confidence DESC → not generic prefix.
    """
    if not candidates:
        return None
    # Penalize generic prefixes (hello@, info@, etc.)
    def sort_key(c: dict):
        generic_penalty = 1 if _GENERIC_PREFIXES.match(c["email"]) else 0
        return (c["title_score"], generic_penalty, -c["confidence"])
    candidates.sort(key=sort_key)
    return candidates[0]


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────

def enrich_email(company: dict) -> dict:
    """
    Run the multi-provider waterfall for a single company dict.

    Input fields used:
      - website       : company website (required to extract domain)
      - company_name  : human-readable name (optional, improves results)
      - stakeholder_name : name already found by website scraping (optional)
      - linkedin_url  : company or person LinkedIn URL (optional)

    Returns a dict with keys:
      stakeholder_email, stakeholder_name, stakeholder_title,
      email_confidence, email_verified, email_provider
    """
    empty = {
        "stakeholder_email": None,
        "stakeholder_name": None,
        "stakeholder_title": None,
        "email_confidence": None,
        "email_verified": False,
        "email_provider": None,
    }

    website = company.get("website") or ""
    if not website:
        return empty

    domain = _domain_from(website)
    if not domain:
        return empty

    company_name = company.get("company_name") or ""
    existing_name = company.get("stakeholder_name") or ""
    linkedin_url = company.get("linkedin_url") or ""
    candidates: list[dict] = []

    # ── Phase 1: Domain-based searches (no name needed) ──────────────────────
    candidates.extend(_prospeo_domain_search(domain, company_name))

    # If Prospeo didn't return a good high-priority result, try name-based APIs
    best_so_far = _pick_best(candidates)
    need_name_search = not best_so_far or best_so_far["title_score"] > 5

    # ── Phase 2: Name-based searches ─────────────────────────────────────────
    if need_name_search and existing_name:
        first, last = _split_name(existing_name)
        if first and last:
            candidates.extend(_findymail_find(existing_name, domain))
            candidates.extend(_icypeas_search(first, last, domain))
            candidates.extend(_leadmagic_find(first, last, company_name, domain))

    # ── Phase 3: LinkedIn-based searches ─────────────────────────────────────
    if linkedin_url:
        candidates.extend(_wiza_linkedin(linkedin_url))
        candidates.extend(_contactout_linkedin(linkedin_url))

    best = _pick_best(candidates)
    if not best:
        return empty

    # ── Phase 4: Verification ─────────────────────────────────────────────────
    is_valid, confidence = verify_email(best["email"])
    if not is_valid:
        # Remove invalid, try next
        candidates = [c for c in candidates if c["email"] != best["email"]]
        best = _pick_best(candidates)
        if not best:
            return empty
        is_valid, confidence = verify_email(best["email"])

    return {
        "stakeholder_email": best["email"],
        "stakeholder_name": best["name"],
        "stakeholder_title": best["title"],
        "email_confidence": round(max(confidence, best["confidence"]), 3),
        "email_verified": is_valid,
        "email_provider": best["provider"],
    }


def verify_existing_email(company: dict) -> dict:
    """
    Verify an already-found email and return updated confidence/verified fields.
    """
    email = company.get("stakeholder_email") or ""
    if not email:
        return {}
    is_valid, confidence = verify_email(email)
    return {
        "email_verified": is_valid,
        "email_confidence": round(confidence, 3),
        "email_provider": company.get("email_provider") or "scraped",
    }
