"""
Run this script LOCALLY to create Gmail drafts from data/emails_to_draft.json.

Steps:
  1. python auth_gmail.py          # generates auth URL (first time only)
  2. python auth_gmail.py <code>   # saves token.json (first time only)
  3. python create_drafts_local.py # creates all drafts in Gmail

Then review in Gmail and send with:
  python update_drafts.py --send-drafts
"""

import json
import os
import sys

from src.sender.gmail_sender import (
    GMAIL_API,
    build_mime_email,
    get_gmail_service,
    get_gmail_signature,
)
from config.settings import FOLLOWUP_LOG_PATH

EXPORT_FILE = "data/emails_to_draft.json"


def main():
    if not os.path.exists(EXPORT_FILE):
        print(f"[ERROR] {EXPORT_FILE} not found.")
        sys.exit(1)

    with open(EXPORT_FILE, encoding="utf-8") as f:
        emails = json.load(f)

    print(f"  Loaded {len(emails)} emails from {EXPORT_FILE}")
    service = get_gmail_service()
    signature_html = get_gmail_signature(service)

    # Load already-logged contacts to avoid duplicate followup entries
    import csv as _csv
    already_logged: set = set()
    if os.path.exists(FOLLOWUP_LOG_PATH):
        with open(FOLLOWUP_LOG_PATH, encoding="utf-8") as f:
            for row in _csv.DictReader(f):
                already_logged.add(row.get("stakeholder_email", "").strip().lower())

    success, fail = 0, 0
    followup_records = []
    from datetime import datetime
    now_ts = datetime.now().isoformat()

    for company in emails:
        email_addr = company.get("stakeholder_email", "").strip()
        if not email_addr:
            continue
        try:
            raw = build_mime_email(company, signature_html)
            resp = service.post(
                f"{GMAIL_API}/drafts",
                json={"message": {"raw": raw}},
            )
            resp.raise_for_status()
            draft_id = resp.json().get("id", "?")
            success += 1
            print(f"  [DRAFT] {company.get('company_name')} → {email_addr}  (id: {draft_id})")

            if email_addr.lower() not in already_logged:
                followup_records.append({
                    "company_name": company.get("company_name", ""),
                    "website": company.get("website", ""),
                    "stakeholder_name": company.get("stakeholder_name", ""),
                    "stakeholder_email": email_addr,
                    "industry": company.get("industry", ""),
                    "initial_sent_at": now_ts,
                    "replied": "no",
                    "fu1_sent_at": "",
                    "fu2_sent_at": "",
                    "fu3_sent_at": "",
                })
        except Exception as e:
            fail += 1
            print(f"  [FAIL] {company.get('company_name')}: {e}")

    print(f"\n  Done — Created: {success}  |  Failed: {fail}")

    if followup_records:
        fu_fieldnames = [
            "company_name", "website", "stakeholder_name", "stakeholder_email",
            "industry", "initial_sent_at", "replied",
            "fu1_sent_at", "fu2_sent_at", "fu3_sent_at",
        ]
        os.makedirs(os.path.dirname(FOLLOWUP_LOG_PATH), exist_ok=True)
        file_exists = os.path.exists(FOLLOWUP_LOG_PATH)
        with open(FOLLOWUP_LOG_PATH, "a", newline="", encoding="utf-8") as f:
            writer = _csv.DictWriter(f, fieldnames=fu_fieldnames, extrasaction="ignore")
            if not file_exists:
                writer.writeheader()
            writer.writerows(followup_records)
        print(f"  Logged {len(followup_records)} contact(s) to {FOLLOWUP_LOG_PATH}")

    if success:
        print("\n  → Review the drafts in Gmail, then send with:")
        print("     python update_drafts.py --send-drafts")


if __name__ == "__main__":
    main()
