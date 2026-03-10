"""
Update Gmail Drafts from a review CSV, applying content fixes on the fly.

Fixes applied automatically:
  - Removes all em-dashes (' — ') from email bodies (replaced with ', ')
  - Removes all em-dashes (' — ') from email subjects (replaced with ': ')
  - Corrects garbled company names caused by scraping artifacts

Steps:
  1. Load approved rows from the review CSV and apply fixes in memory
  2. Authenticate with Gmail
  3. Delete ALL existing drafts in the account
  4. Create new drafts from the fixed data

Usage:
  python update_drafts.py
  python update_drafts.py --review-file review/review_20260310_032500.csv
"""

import argparse
import csv
import os
import sys

from src.sender.gmail_sender import get_gmail_service, build_mime_email, get_gmail_signature, GMAIL_API

DEFAULT_REVIEW_FILE = "review/review_20260310_032500.csv"

# Scraping artifacts → clean company names
NAME_FIXES = {
    "Tidal Control B.V.Security Compliance Automation Platform": "Tidal Control",
    "Solease B.V.Zonnestroom voor particulieren.": "Solease",
    "Maria01Lapinlahdenkatu 1600180 HelsinkiFinland": "Maria01",
    "AldaraAllActiveWe triedModern homeowner management": "Lumo",
}


def load_and_fix_approved(review_file: str) -> list:
    """Load approved rows from review CSV and apply content fixes in memory."""
    if not os.path.exists(review_file):
        print(f"  [ERROR] Review file not found: {review_file}")
        return []

    approved = []
    with open(review_file, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("review_status", "").strip().lower() != "approved":
                continue
            r = dict(row)
            old_name = r["company_name"]
            new_name = NAME_FIXES.get(old_name, old_name)
            r["company_name"] = new_name
            # Fix subject: ' — ' → ': ', then fix company name
            r["email_subject"] = r["email_subject"].replace(" — ", ": ").replace(old_name, new_name)
            # Fix body: ' — ' → ', ', then fix company name
            r["email_body"] = r["email_body"].replace(" — ", ", ").replace(old_name, new_name)
            approved.append(r)

    return approved


def delete_all_drafts(service) -> int:
    """Delete every draft in the authenticated Gmail account. Returns count deleted."""
    deleted = 0
    page_token = None

    while True:
        params = {"maxResults": 100}
        if page_token:
            params["pageToken"] = page_token

        resp = service.get(f"{GMAIL_API}/drafts", params=params)
        resp.raise_for_status()
        data = resp.json()

        drafts = data.get("drafts", [])
        for draft in drafts:
            del_resp = service.delete(f"{GMAIL_API}/drafts/{draft['id']}")
            if del_resp.status_code in (200, 204):
                deleted += 1
            else:
                print(f"  [WARN] Could not delete draft {draft['id']}: {del_resp.status_code}")

        page_token = data.get("nextPageToken")
        if not page_token:
            break

    return deleted


def create_drafts(service, approved: list, signature_html: str) -> tuple[int, int]:
    """Create Gmail drafts for all approved emails. Returns (success, fail)."""
    success, fail = 0, 0

    for company in approved:
        email_addr = company.get("stakeholder_email", "").strip()
        if not email_addr:
            print(f"  [SKIP] {company.get('company_name')} — no email address")
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
        except Exception as e:
            fail += 1
            print(f"  [FAIL] {company.get('company_name')}: {e}")

    return success, fail


def main():
    parser = argparse.ArgumentParser(description="Delete existing Gmail drafts and recreate with fixed content.")
    parser.add_argument(
        "--review-file",
        default=DEFAULT_REVIEW_FILE,
        help=f"Path to the review CSV (default: {DEFAULT_REVIEW_FILE})",
    )
    args = parser.parse_args()

    print(f"\n  Review file: {args.review_file}")

    approved = load_and_fix_approved(args.review_file)
    if not approved:
        print("  [ERROR] No approved emails found in the review file.")
        sys.exit(1)

    print(f"  Approved emails to recreate: {len(approved)}")

    confirm = input(
        "\n  This will DELETE all existing drafts in your Gmail account\n"
        "  and recreate them from the fixed CSV.\n"
        "  Type 'yes' to continue: "
    ).strip().lower()

    if confirm != "yes":
        print("  Aborted.")
        sys.exit(0)

    print("\n=== Step 1: Authenticate ===")
    service = get_gmail_service()
    signature_html = get_gmail_signature(service)

    print("\n=== Step 2: Delete existing drafts ===")
    deleted = delete_all_drafts(service)
    print(f"  Deleted: {deleted} draft(s)")

    print(f"\n=== Step 3: Create {len(approved)} new draft(s) ===")
    success, fail = create_drafts(service, approved, signature_html)

    print(f"\n  Done — Created: {success}  |  Failed: {fail}")
    if success:
        print("  → Open Gmail → Drafts to review and send each email.")


if __name__ == "__main__":
    main()
