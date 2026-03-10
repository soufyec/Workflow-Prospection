"""
Follow-up email system for Dõlmen Studios prospection pipeline.

Workflow:
  1. Run: python main.py --step followup
  2. The system reads followup_log.csv (populated by --step draft)
  3. For each contact with a due follow-up, it creates a Gmail draft
  4. Review the drafts in Gmail — delete the ones you don't want to send
  5. Send the rest with: python update_drafts.py --send-drafts

  To permanently skip a contact, set replied=yes in followup_log.csv.

Timing (configurable via .env):
  FOLLOWUP_1_DAYS=5   (default)
  FOLLOWUP_2_DAYS=12  (default)
  FOLLOWUP_3_DAYS=21  (default)
"""

import csv
import os
from datetime import datetime, timezone
from typing import Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape

from config.settings import (
    FOLLOWUP_LOG_PATH,
    FOLLOWUP_1_DAYS,
    FOLLOWUP_2_DAYS,
    FOLLOWUP_3_DAYS,
    FOLLOWUP_1_TEMPLATE_PATH,
    FOLLOWUP_2_TEMPLATE_PATH,
    FOLLOWUP_3_TEMPLATE_PATH,
    SENDER_EMAIL,
    SENDER_NAME,
)
from src.sender.gmail_sender import (
    GMAIL_API,
    build_mime_email,
    get_gmail_service,
    get_gmail_signature,
)

_FIELDNAMES = [
    "company_name", "website", "stakeholder_name", "stakeholder_email",
    "industry", "initial_sent_at", "replied",
    "fu1_sent_at", "fu2_sent_at", "fu3_sent_at",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_followup_log() -> list[dict]:
    if not os.path.exists(FOLLOWUP_LOG_PATH):
        return []
    with open(FOLLOWUP_LOG_PATH, "r", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _save_followup_log(records: list[dict]) -> None:
    os.makedirs(os.path.dirname(FOLLOWUP_LOG_PATH), exist_ok=True)
    with open(FOLLOWUP_LOG_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)


def _days_since(ts_str: str) -> Optional[float]:
    """Return days elapsed since an ISO timestamp, or None if empty/invalid."""
    if not ts_str:
        return None
    try:
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return (now - dt).total_seconds() / 86400
    except ValueError:
        return None


def _render_followup(template_path: str, company: dict) -> str:
    """Render a follow-up template and return the HTML body string."""
    from src.generator.email_generator import get_first_name, get_industry_label

    template_dir = os.path.dirname(os.path.abspath(template_path))
    template_file = os.path.basename(template_path)
    env = Environment(
        loader=FileSystemLoader(template_dir),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template(template_file)
    return template.render(
        recipient_name=get_first_name(company.get("stakeholder_name")),
        company_name=company.get("company_name", "your company"),
        industry_label=get_industry_label(company.get("industry", "")),
        sender_name=SENDER_NAME,
        sender_email=SENDER_EMAIL,
    )


def _create_draft(service, company: dict, body_html: str, subject: str, signature_html: str) -> str:
    """Create a Gmail draft and return its ID."""
    company_for_mime = dict(company)
    company_for_mime["email_subject"] = subject
    company_for_mime["email_body"] = body_html
    raw = build_mime_email(company_for_mime, signature_html)
    resp = service.post(f"{GMAIL_API}/drafts", json={"message": {"raw": raw}})
    resp.raise_for_status()
    return resp.json().get("id", "?")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_followups() -> None:
    """
    For each contact in followup_log.csv, create a Gmail draft for whichever
    follow-up is due. No reply detection — review and delete unwanted drafts
    in Gmail before running --send-drafts.

    To permanently exclude a contact, set replied=yes in followup_log.csv.
    """
    records = _load_followup_log()
    if not records:
        print(
            "  [INFO] followup_log.csv is empty or missing.\n"
            "         Run --step draft first to populate it."
        )
        return

    print(f"  Loaded {len(records)} contact(s) from followup log.")
    service = get_gmail_service()
    signature_html = get_gmail_signature(service)

    fu_configs = [
        ("fu1_sent_at", FOLLOWUP_1_DAYS, FOLLOWUP_1_TEMPLATE_PATH, "FU1"),
        ("fu2_sent_at", FOLLOWUP_2_DAYS, FOLLOWUP_2_TEMPLATE_PATH, "FU2"),
        ("fu3_sent_at", FOLLOWUP_3_DAYS, FOLLOWUP_3_TEMPLATE_PATH, "FU3"),
    ]

    drafted = 0
    skipped_excluded = 0
    skipped_not_due = 0
    skipped_done = 0
    errors = 0

    for rec in records:
        email_addr = rec.get("stakeholder_email", "").strip()
        company_name = rec.get("company_name", "?")

        if not email_addr:
            continue

        # Skip contacts manually marked as excluded
        if rec.get("replied", "no").lower() == "yes":
            skipped_excluded += 1
            continue

        # All 3 follow-ups already drafted
        if rec.get("fu3_sent_at"):
            skipped_done += 1
            continue

        # Determine which follow-up is next and whether it's due
        for fu_field, threshold_days, template_path, label in fu_configs:
            if rec.get(fu_field):
                continue  # Already drafted this one

            # Clock starts from the previous step
            if label == "FU1":
                reference_ts = rec.get("initial_sent_at", "")
            elif label == "FU2":
                reference_ts = rec.get("fu1_sent_at", "")
            else:  # FU3
                reference_ts = rec.get("fu2_sent_at", "")

            days_elapsed = _days_since(reference_ts)
            if days_elapsed is None or days_elapsed < threshold_days:
                due_in = threshold_days - (days_elapsed or 0)
                print(
                    f"  [WAIT]  {company_name} — {label} not due yet "
                    f"(due in ~{due_in:.1f} day(s))"
                )
                skipped_not_due += 1
                break  # Don't check later follow-ups for this contact

            # Due — create the draft
            try:
                body_html = _render_followup(template_path, rec)
                subject = f"Re: Branding opportunity — {company_name} × Dõlmen Studios"
                draft_id = _create_draft(service, rec, body_html, subject, signature_html)
                rec[fu_field] = datetime.now().isoformat()
                drafted += 1
                print(
                    f"  [DRAFT] {company_name} → {email_addr}  "
                    f"({label}, draft id: {draft_id})"
                )
            except Exception as exc:
                errors += 1
                print(f"  [FAIL]  {company_name} {label}: {exc}")
            break  # Only one follow-up per contact per run

    _save_followup_log(records)

    print(f"\n  Follow-up drafts created: {drafted}")
    print(f"  Not due yet:              {skipped_not_due}")
    print(f"  All follow-ups done:      {skipped_done}")
    if skipped_excluded:
        print(f"  Excluded (replied=yes):   {skipped_excluded}")
    if errors:
        print(f"  Errors:                   {errors}")
    if drafted:
        print("\n  → Delete unwanted drafts in Gmail, then send the rest with:")
        print("     python update_drafts.py --send-drafts")
