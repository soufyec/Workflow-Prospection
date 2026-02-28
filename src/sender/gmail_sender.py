"""
Stage 5: Send approved emails via Gmail API using OAuth2.

Authentication:
- Uses credentials/credentials.json (Desktop App OAuth2 client secret)
- Caches token in credentials/token.json (auto-refreshed when expired)
- On first run, opens a browser window for OAuth2 consent
- Scope: gmail.send only (minimal permission)
"""

import base64
import os
import re
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from config.settings import (
    CREDENTIALS_PATH,
    PIPELINE_PATH,
    SENDER_EMAIL,
    SENDER_NAME,
    SENT_LOG_PATH,
    TOKEN_PATH,
)
from src.utils.deduplication import append_sent_log
from src.utils.pipeline_state import load_state, save_state

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]


def get_gmail_service():
    """
    Authenticate and return a Gmail API service object.

    Token lifecycle:
    - Valid token.json → use directly
    - Expired token with refresh_token → auto-refresh
    - No token → launch browser OAuth2 consent flow
    """
    creds: Optional[Credentials] = None

    if os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CREDENTIALS_PATH):
                raise FileNotFoundError(
                    f"Gmail credentials not found at {CREDENTIALS_PATH}.\n"
                    "See README.md for Gmail OAuth2 setup instructions."
                )
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
            creds = flow.run_local_server(port=0)

        os.makedirs(os.path.dirname(TOKEN_PATH), exist_ok=True)
        with open(TOKEN_PATH, "w") as f:
            f.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)


def _html_to_plain(html: str) -> str:
    """Strip HTML tags to produce a plain-text fallback."""
    text = re.sub(r"<[^>]+>", "", html)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def build_mime_email(company: dict) -> str:
    """
    Build a base64url-encoded MIME email (multipart/alternative with plain-text fallback).
    """
    msg = MIMEMultipart("alternative")
    msg["Subject"] = company["email_subject"]
    msg["From"] = f"{SENDER_NAME} <{SENDER_EMAIL}>"
    msg["To"] = company["stakeholder_email"]

    plain = _html_to_plain(company.get("email_body", ""))
    msg.attach(MIMEText(plain, "plain", "utf-8"))
    msg.attach(MIMEText(company.get("email_body", ""), "html", "utf-8"))

    return base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")


def send_approved_emails(review_file: str) -> None:
    """
    Entry point for Stage 5.

    1. Load approved companies from the review CSV
    2. Authenticate Gmail API
    3. Send each email; log success/failure to sent_log.csv
    4. Update pipeline state
    """
    from src.reviewer.review import load_approved_from_review

    approved = load_approved_from_review(review_file)
    if not approved:
        print("  [INFO] No approved emails found in the review file.")
        print("  Open the review CSV, set review_status = 'approved', save, and re-run.")
        return

    print(f"  Found {len(approved)} approved email(s) to send.")
    service = get_gmail_service()
    state = load_state(PIPELINE_PATH)

    sent_records = []
    success_count = 0
    fail_count = 0

    for company in approved:
        email_addr = company.get("stakeholder_email", "").strip()
        if not email_addr:
            print(f"  [SKIP] {company.get('company_name')} — no email address")
            continue

        try:
            raw = build_mime_email(company)
            service.users().messages().send(
                userId="me",
                body={"raw": raw},
            ).execute()

            sent_records.append({
                "timestamp": datetime.now().isoformat(),
                "company_name": company.get("company_name", ""),
                "website": company.get("website", ""),
                "stakeholder_email": email_addr,
                "stakeholder_name": company.get("stakeholder_name", ""),
                "status": "sent",
                "error": "",
            })
            state.setdefault("sent", []).append(company)
            success_count += 1
            print(f"  [SENT] {company.get('company_name')} → {email_addr}")

        except HttpError as e:
            fail_count += 1
            sent_records.append({
                "timestamp": datetime.now().isoformat(),
                "company_name": company.get("company_name", ""),
                "website": company.get("website", ""),
                "stakeholder_email": email_addr,
                "stakeholder_name": company.get("stakeholder_name", ""),
                "status": "error",
                "error": str(e),
            })
            print(f"  [FAIL] {company.get('company_name')}: {e}")

    append_sent_log(SENT_LOG_PATH, sent_records)
    save_state(PIPELINE_PATH, state)

    print(f"\n  Sent: {success_count}  |  Failed: {fail_count}")
    print(f"  Audit log: {SENT_LOG_PATH}")
