"""
Stage 3: Render personalized emails from the Jinja2 template.

Template variables injected per company:
  recipient_name      - First name of stakeholder (or "there" if unknown)
  company_name        - Target company name
  industry_label      - Human-readable industry name ("SaaS", "fintech", etc.)
  vc_source           - VC firm name (or empty string)
  product_service_line - "Your SaaS product" / "Your financial platform" / etc.
  pain_point          - Industry-specific opportunity sentence
  sender_name         - Dõlmen Studios
  sender_email        - hello@dolmenstudios.com
"""

import os
from typing import Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape

from config.settings import PIPELINE_PATH, SENDER_EMAIL, SENDER_NAME, TEMPLATE_PATH
from src.utils.pipeline_state import load_state, save_state

# Human-readable label used in "While researching X companies, I came across Y"
INDUSTRY_LABELS = {
    "fintech": "fintech",
    "saas": "SaaS",
    "healthtech": "healthcare technology",
    "ecommerce": "e-commerce",
    "cleantech": "clean energy",
    "greentech": "clean energy",
    "edtech": "edtech",
    "deeptech": "deep tech",
    "marketplace": "marketplace",
    "biotech": "biotech",
    "ai": "AI",
    "proptech": "real estate technology",
    "foodtech": "food tech",
    "mobility": "mobility",
    "cybersecurity": "cybersecurity",
    "hrtech": "HR tech",
    "legaltech": "legal tech",
    "martech": "marketing tech",
    "constructiontech": "construction tech",
    "spacetech": "space tech",
    "realestate": "real estate",
    "healthcare": "healthcare",
    "consulting": "B2B consulting",
}

# Opening sentence for the company-specific observation paragraph
PRODUCT_SERVICE_LINES = {
    "fintech": "Your financial platform is solid",
    "saas": "Your SaaS product is solid",
    "healthtech": "Your health solution is solid",
    "healthcare": "Your healthcare offer is solid",
    "ecommerce": "Your e-commerce experience is solid",
    "cleantech": "Your clean energy platform is solid",
    "greentech": "Your clean energy platform is solid",
    "edtech": "Your learning platform is solid",
    "deeptech": "Your technology is solid",
    "marketplace": "Your marketplace is solid",
    "biotech": "Your biotech platform is solid",
    "ai": "Your AI product is solid",
    "proptech": "Your property platform is solid",
    "realestate": "Your real estate offer is solid",
    "foodtech": "Your food tech solution is solid",
    "mobility": "Your mobility solution is solid",
    "cybersecurity": "Your security platform is solid",
    "hrtech": "Your HR platform is solid",
    "legaltech": "Your legal tech product is solid",
    "martech": "Your marketing platform is solid",
    "constructiontech": "Your construction tech solution is solid",
    "spacetech": "Your technology is solid",
    "consulting": "Your consultancy offer is solid",
    "default": "Your product is solid",
}

# The "pain point" — what the brand is missing / where the opportunity lies
PAIN_POINTS = {
    "fintech": (
        "we believe your current brand doesn't yet project the institutional trust "
        "needed to close the enterprise deals your product deserves"
    ),
    "saas": (
        "your visual identity doesn't yet match the quality of what you've built — "
        "and that gap costs you at every enterprise demo and investor conversation"
    ),
    "healthtech": (
        "your digital presence underrepresents the quality of care you deliver, "
        "which can slow trust-building with both patients and institutional partners"
    ),
    "healthcare": (
        "your digital presence underrepresents the quality of care you deliver, "
        "which can slow trust-building with both patients and institutional partners"
    ),
    "ecommerce": (
        "you're likely leaving conversion on the table due to brand inconsistencies "
        "that signal 'startup' when your product is ready to be a market leader"
    ),
    "cleantech": (
        "your mission deserves a visual identity that converts climate-conscious buyers "
        "and ESG-focused investors into long-term advocates"
    ),
    "greentech": (
        "your mission deserves a visual identity that converts climate-conscious buyers "
        "and ESG-focused investors into long-term advocates"
    ),
    "edtech": (
        "learners and institutions make trust decisions in seconds — "
        "and your current brand may not be winning that moment as consistently as you could"
    ),
    "deeptech": (
        "deep-tech companies often communicate complexity when they should be communicating "
        "confidence — and that affects enterprise buy-in and investor narrative"
    ),
    "marketplace": (
        "brand perception drives supply-side trust more than any feature — "
        "and a gap there limits growth on both sides of your marketplace"
    ),
    "biotech": (
        "credibility signals in brand design can change the outcome of partnership "
        "and regulatory conversations before a word is spoken"
    ),
    "ai": (
        "there's an opportunity to make your AI feel more trustworthy and approachable "
        "without compromising the technical credibility you've earned"
    ),
    "proptech": (
        "in property, a brand that signals premium before the first interaction "
        "can justify your pricing and attract higher-quality clients and partners"
    ),
    "realestate": (
        "in luxury real estate, a brand that signals premium before the first interaction "
        "can justify your pricing and attract higher-quality clients"
    ),
    "foodtech": (
        "consumers and retail partners buy the mission as much as the product — "
        "and your brand may not be telling that story clearly enough yet"
    ),
    "mobility": (
        "trust and safety perception are everything in mobility — "
        "and there's room to strengthen how your brand signals both to users and regulators"
    ),
    "cybersecurity": (
        "in security, brand credibility is your first line of trust with enterprise buyers, "
        "and a perception gap at that level can be costly"
    ),
    "hrtech": (
        "HR platforms succeed when they feel human — "
        "and there's an opportunity to strengthen how your brand connects emotionally "
        "with HR leaders and their teams"
    ),
    "legaltech": (
        "communicating precision and modernity simultaneously is the legaltech branding "
        "challenge — and it's where many platforms leave value on the table"
    ),
    "martech": (
        "marketing platforms are judged by their own marketing first — "
        "and there's an opportunity to raise the bar on how your brand presents itself "
        "to the CMOs and marketing directors you're selling to"
    ),
    "constructiontech": (
        "construction tech is still early — the brands that establish visual authority now "
        "will own the category, and design signals innovation in an industry hungry for it"
    ),
    "spacetech": (
        "space tech brands that project ambition, precision, and credibility "
        "attract the institutional partners and government contracts that define scale"
    ),
    "consulting": (
        "potential clients make a quality judgement about your expertise "
        "before the first call — and your current brand may not be winning that moment"
    ),
    "default": (
        "we believe there's a meaningful opportunity to strengthen your visual identity "
        "and messaging to match the quality of your underlying business"
    ),
}


def _get_by_industry(mapping: dict, industry: str) -> str:
    """Look up a value from an industry mapping, with fallback to 'default'."""
    industry_lower = (industry or "").lower()
    for key in mapping:
        if key in industry_lower:
            return mapping[key]
    return mapping["default"]


def get_industry_label(industry: str) -> str:
    industry_lower = (industry or "").lower()
    for key, label in INDUSTRY_LABELS.items():
        if key in industry_lower:
            return label
    return "technology"


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

    industry = company.get("industry", "")
    company_name = company.get("company_name", "your company")
    vc_source = company.get("vc_source", "")

    context = {
        "sender_name": SENDER_NAME,
        "sender_email": SENDER_EMAIL,
        "recipient_name": get_first_name(company.get("stakeholder_name")),
        "company_name": company_name,
        "industry_label": get_industry_label(industry),
        "vc_source": vc_source,
        "product_service_line": _get_by_industry(PRODUCT_SERVICE_LINES, industry),
        "pain_point": _get_by_industry(PAIN_POINTS, industry),
    }

    body = template.render(**context)
    subject = f"Branding opportunity — {company_name} × Dõlmen Studios"
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
