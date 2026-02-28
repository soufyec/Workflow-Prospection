"""
Stage 3: Render personalized emails from the Jinja2 template.

Template variables injected per company:
  recipient_name  - First name of stakeholder (or "there" if unknown)
  company_name    - Target company name
  industry        - Sector/industry from VC metadata
  vc_source       - VC firm name (used as social proof hook)
  funding_stage   - pre-seed / seed / Series A
  industry_hook   - Dynamic one-liner tailored to the company's sector
  sender_name     - Dõlmen Studios
  sender_email    - hello@dolmenstudios.com
"""

import os
from typing import Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape

from config.settings import PIPELINE_PATH, SENDER_EMAIL, SENDER_NAME, TEMPLATE_PATH
from src.utils.pipeline_state import load_state, save_state

INDUSTRY_HOOKS = {
    "fintech": (
        "We've seen how fintech brands that nail their visual identity "
        "convert significantly better at every funnel stage — "
        "trust is your most valuable asset."
    ),
    "saas": (
        "Early-stage SaaS teams that invest in brand early tend to close "
        "enterprise deals faster. A polished identity signals maturity "
        "before a single demo call."
    ),
    "healthtech": (
        "In health-tech, trust is everything. A cohesive brand signals "
        "credibility and safety before a single word is read — "
        "patients and buyers both notice."
    ),
    "ecommerce": (
        "In crowded e-commerce, a distinctive brand is the only moat "
        "that can't be copied overnight. Design is retention before it's acquisition."
    ),
    "cleantech": (
        "Climate-tech brands that communicate their mission visually "
        "attract better talent, better press, and easier follow-on rounds."
    ),
    "edtech": (
        "EdTech products that feel intuitive and trustworthy see "
        "dramatically higher course completion and renewal rates."
    ),
    "deeptech": (
        "Deep-tech companies often underinvest in brand — which means "
        "the ones that don't stand out immediately to investors and enterprise buyers."
    ),
    "marketplace": (
        "Marketplace businesses live or die on supply-side trust. "
        "Brand design is often the fastest lever to improve both sides of that equation."
    ),
    "biotech": (
        "In biotech, credibility is everything. A brand that signals "
        "rigour and ambition can change the outcome of a partnership conversation."
    ),
    "default": (
        "Brands that invest early in distinctive design consistently "
        "outperform peers on customer acquisition costs and investor confidence."
    ),
}


def get_industry_hook(industry: str) -> str:
    """Return the most relevant industry hook based on sector keywords."""
    industry_lower = (industry or "").lower()
    for key, hook in INDUSTRY_HOOKS.items():
        if key in industry_lower:
            return hook
    return INDUSTRY_HOOKS["default"]


def get_first_name(full_name: Optional[str]) -> str:
    """Extract first name, or return 'there' if name is unknown."""
    if not full_name or not full_name.strip():
        return "there"
    return full_name.strip().split()[0]


def render_email(company: dict) -> dict:
    """
    Render subject + HTML body for one company record using the Jinja2 template.
    Returns dict: { "email_subject": str, "email_body": str }
    """
    template_dir = os.path.dirname(os.path.abspath(TEMPLATE_PATH))
    template_file = os.path.basename(TEMPLATE_PATH)

    env = Environment(
        loader=FileSystemLoader(template_dir),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template(template_file)

    context = {
        "sender_name": SENDER_NAME,
        "sender_email": SENDER_EMAIL,
        "recipient_name": get_first_name(company.get("stakeholder_name")),
        "company_name": company.get("company_name", "your company"),
        "industry": company.get("industry", ""),
        "vc_source": company.get("vc_source", "a top investor"),
        "funding_stage": company.get("funding_stage", "seed"),
        "industry_hook": get_industry_hook(company.get("industry", "")),
    }

    body = template.render(**context)
    subject = f"Brand partnership opportunity for {company.get('company_name', 'your team')}"
    return {"email_subject": subject, "email_body": body}


def generate_emails() -> list:
    """
    Entry point for Stage 3.
    Loads enriched companies (those with a stakeholder_email found),
    renders a personalized email for each, and saves to pipeline state.
    """
    state = load_state(PIPELINE_PATH)
    enriched = state.get("enriched", [])
    generated_websites = {c["website"] for c in state.get("generated", [])}

    generated = list(state.get("generated", []))
    skipped_no_email = 0

    for company in enriched:
        if not company.get("stakeholder_email"):
            skipped_no_email += 1
            continue
        if company["website"] in generated_websites:
            continue

        rendered = render_email(company)
        company_copy = dict(company)
        company_copy.update(rendered)
        generated.append(company_copy)

    state["generated"] = generated
    save_state(PIPELINE_PATH, state)

    print(f"  Generated {len(generated)} emails.")
    if skipped_no_email:
        print(f"  Skipped {skipped_no_email} companies with no email found.")
    return generated
