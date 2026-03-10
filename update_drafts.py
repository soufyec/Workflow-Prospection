"""
Update Gmail Drafts from fixed review CSV.

Steps:
  1. Authenticate with Gmail
  2. Delete ALL existing drafts in the account
  3. Create new drafts from the approved rows in the fixed CSV

Usage:
  python update_drafts.py
  python update_drafts.py --review-file review/review_20260310_032500_fixed.csv
"""

import argparse
import sys

from src.sender.gmail_sender import get_gmail_service, build_mime_email, get_gmail_signature, GMAIL_API
from src.reviewer.review import load_approved_from_review
from config.settings import PIPELINE_PATH
from src.utils.pipeline_state import load_state

DEFAULT_REVIEW_FILE = "review/review_20260310_032500_fixed.csv"


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
    parser = argparse.ArgumentParser(description="Delete existing Gmail drafts and recreate from fixed CSV.")
    parser.add_argument(
        "--review-file",
        default=DEFAULT_REVIEW_FILE,
        help=f"Path to the fixed review CSV (default: {DEFAULT_REVIEW_FILE})",
    )
    args = parser.parse_args()

    print(f"\n  Review file: {args.review_file}")

    approved = load_approved_from_review(args.review_file)
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
