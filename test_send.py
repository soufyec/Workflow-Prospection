"""
Test script: sends the first approved email to soufyess@gmail.com immediately.
Prefixes subject with [TEST] so you know it's a test.
Run: python test_send.py
"""

import csv
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

import json

from src.sender.gmail_sender import get_gmail_service, get_gmail_signature, build_mime_email, GMAIL_API

TEST_RECIPIENT = "soufyess@gmail.com"

# Resolve review file from pipeline state (same logic as --step send)
with open("data/pipeline.json", encoding="utf-8") as _f:
    _state = json.load(_f)
REVIEW_FILE = _state.get("review_file")
if not REVIEW_FILE or not os.path.exists(REVIEW_FILE):
    print(f"[ERROR] Review file not found: {REVIEW_FILE!r}")
    print("  Run --step review first.")
    sys.exit(1)

print(f"  Using review file: {REVIEW_FILE}")

# ---------------------------------------------------------------------------
# Load first approved row
# ---------------------------------------------------------------------------
with open(REVIEW_FILE, newline="", encoding="utf-8") as f:
    reader = csv.DictReader(f)
    company = next(
        (row for row in reader if row["review_status"] == "approved"),
        None,
    )

if not company:
    print("[ERROR] No approved rows found in the review file.")
    sys.exit(1)

# Override recipient + mark as test
company = dict(company)
original_recipient = company["stakeholder_email"]
company["stakeholder_email"] = TEST_RECIPIENT
company["email_subject"] = f"[TEST] {company['email_subject']}"

print(f"\n  Company : {company['company_name']}")
print(f"  Original: {original_recipient}")
print(f"  Sending to (test): {TEST_RECIPIENT}")
print(f"  Subject : {company['email_subject']}\n")

# ---------------------------------------------------------------------------
# Authenticate + fetch Gmail signature (Soufyan Signature)
# ---------------------------------------------------------------------------
service = get_gmail_service()
signature_html = get_gmail_signature(service)

# ---------------------------------------------------------------------------
# Build + send
# ---------------------------------------------------------------------------
raw = build_mime_email(company, signature_html)

resp = service.post(
    f"{GMAIL_API}/messages/send",
    json={"raw": raw},
)

if resp.status_code == 200:
    print(f"  [SENT] Test email delivered to {TEST_RECIPIENT}")
else:
    print(f"  [ERROR] {resp.status_code}: {resp.text}")
    sys.exit(1)
