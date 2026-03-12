"""
Brand Audit: Score a company's brand/web presence using 5 heuristic criteria.

Checklist (each criterion = 0 or 1 point):
  1. Value clarity   — understood what they do in ~10 seconds (clear H1/hero copy)
  2. Sales-oriented  — website has CTAs and conversion pathways
  3. Brand system    — logo present, visual coherence (CSS vars, consistent palette)
  4. Scalable message— meta description / positioning signals growth / enterprise
  5. Social presence — active social links (LinkedIn, Instagram, Twitter…)

Total: 0–5.  Only leads with score >= MIN_SCORE (default 3) are kept.

Note: A score of 3–4 is the sweet spot — the company has *some* quality signals
(they're not bankrupt) but clear brand gaps that Dõlmen can fix.
"""

import re
from typing import Optional

from bs4 import BeautifulSoup

from src.utils.http_client import fetch_page, fetch_page_playwright, needs_js_rendering

DEFAULT_MIN_SCORE = 3


# ──────────────────────────────────────────────────────────────────────────────
# Individual criterion scorers
# ──────────────────────────────────────────────────────────────────────────────

def _score_value_clarity(soup: BeautifulSoup) -> int:
    """1 if first H1/H2 conveys a real message; 0 if absent or vague."""
    VAGUE = {"welcome", "home", "coming soon", "under construction",
              "innovation", "solutions", "hello", "hi there"}
    hero_texts = [
        tag.get_text(strip=True)
        for tag in soup.find_all(["h1", "h2"])[:3]
        if tag.get_text(strip=True)
    ]
    if not hero_texts:
        return 0
    first = hero_texts[0].lower()
    if any(v in first for v in VAGUE) or len(first.split()) < 3:
        return 0
    return 1


def _score_sales_web(soup: BeautifulSoup) -> int:
    """1 if the page has at least one meaningful CTA button/link."""
    CTA_KEYWORDS = {
        "contact", "get started", "book", "schedule", "request",
        "free", "demo", "buy", "shop", "quote", "hire", "try", "start",
        "learn more", "get in touch", "reach out",
    }
    for el in soup.find_all(["button", "a"]):
        text = el.get_text(strip=True).lower()
        if any(k in text for k in CTA_KEYWORDS) and len(text) < 60:
            return 1
    return 0


def _score_brand_system(soup: BeautifulSoup, raw_html: str) -> int:
    """1 if there are signs of a visual system (logo, CSS vars, consistent palette)."""
    has_logo = bool(
        soup.find("img", {"alt": re.compile(r"logo", re.I)})
        or soup.find("img", {"class": re.compile(r"logo", re.I)})
        or soup.find("svg", {"class": re.compile(r"logo", re.I)})
        or soup.find("a", {"class": re.compile(r"logo|brand", re.I)})
    )
    has_css_vars = "--" in raw_html and ":root" in raw_html
    return 1 if (has_logo or has_css_vars) else 0


def _score_scalable_message(soup: BeautifulSoup) -> int:
    """1 if meta description / OG description suggests strategic, growth-oriented copy."""
    texts = []
    for attr in [("meta", {"name": "description"}), ("meta", {"property": "og:description"})]:
        tag = soup.find(attr[0], attr[1])
        if tag and tag.get("content"):
            texts.append(tag["content"])
    full = " ".join(texts).lower()
    GROWTH_SIGNALS = {
        "scale", "grow", "partner", "enterprise", "platform", "global",
        "transform", "leading", "trusted", "expert", "specialist", "premium",
        "award", "innovative", "results",
    }
    if any(s in full for s in GROWTH_SIGNALS) and len(full) > 60:
        return 1
    # Fallback: if they have a non-trivial meta at all, give partial credit
    if full and len(full) > 40:
        return 1
    return 0


def _score_social_presence(soup: BeautifulSoup) -> int:
    """1 if the page links to at least one social network."""
    SOCIAL = {"linkedin.com", "instagram.com", "twitter.com", "x.com",
               "facebook.com", "tiktok.com", "youtube.com"}
    for link in soup.find_all("a", href=True):
        href = link["href"].lower()
        if any(s in href for s in SOCIAL):
            return 1
    return 0


# ──────────────────────────────────────────────────────────────────────────────
# Brand problem narrative
# ──────────────────────────────────────────────────────────────────────────────

def _build_brand_problem(scores: dict) -> str:
    """Return a 1–2 sentence description of the most important brand issues."""
    issues = []
    if not scores["value_clarity"]:
        issues.append("it's not immediately clear what they do or who they serve")
    if not scores["sales_web"]:
        issues.append("the website lacks clear calls-to-action and conversion pathways")
    if not scores["brand_system"]:
        issues.append("the brand appears as disconnected pieces rather than a coherent visual system")
    if not scores["scalable_message"]:
        issues.append("the messaging doesn't position them for growth or premium clients")
    if not scores["social_presence"]:
        issues.append("minimal or no visible social presence")

    if not issues:
        return "Brand appears functional but likely lacks the strategic depth for premium positioning."

    primary = issues[0].capitalize() + "."
    secondary = f" Also: {issues[1]}." if len(issues) > 1 else ""
    return primary + secondary


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────

def audit_brand(company: dict) -> dict:
    """
    Run the 5-criteria brand audit on a company's homepage.

    Returns the company dict updated with:
      checklist_score (int 0–5)
      brand_problem   (str)
      checklist_detail (dict with per-criterion scores)
    """
    website = company.get("website", "")
    if not website:
        return {**company, "checklist_score": 0,
                "brand_problem": "No website.", "checklist_detail": {}}

    html = fetch_page(website, delay_min=0.5, delay_max=1.5, timeout=10)
    if html and needs_js_rendering(html):
        html = fetch_page_playwright(website)
    if not html:
        return {**company, "checklist_score": 0,
                "brand_problem": "Website unreachable.", "checklist_detail": {}}

    soup = BeautifulSoup(html, "html.parser")
    scores = {
        "value_clarity":    _score_value_clarity(soup),
        "sales_web":        _score_sales_web(soup),
        "brand_system":     _score_brand_system(soup, html),
        "scalable_message": _score_scalable_message(soup),
        "social_presence":  _score_social_presence(soup),
    }
    total = sum(scores.values())
    brand_problem = _build_brand_problem(scores)

    return {
        **company,
        "checklist_score": total,
        "brand_problem": brand_problem,
        "checklist_detail": scores,
    }


def run_brand_audit(companies: list, min_score: int = DEFAULT_MIN_SCORE) -> tuple:
    """
    Audit a list of companies and filter by min_score.

    Returns:
        (kept, discarded_count) — kept is a list of audited dicts with score >= min_score
    """
    kept = []
    discarded = 0

    for company in companies:
        name = company.get("company_name", "?")
        print(f"  [AUDIT] {name}  ({company.get('website', '')})")
        audited = audit_brand(company)
        score = audited.get("checklist_score", 0)
        detail = audited.get("checklist_detail", {})
        detail_str = " | ".join(
            f"{k[:4]}={'✓' if v else '✗'}"
            for k, v in detail.items()
        )
        print(f"    → {score}/5  [{detail_str}]")
        print(f"    → {audited.get('brand_problem', '')[:100]}")

        if score >= min_score:
            kept.append(audited)
        else:
            discarded += 1
            print(f"    → DISCARDED (score {score} < {min_score})")

    print(f"\n  [AUDIT] Kept: {len(kept)} | Discarded: {discarded}")
    return kept, discarded
