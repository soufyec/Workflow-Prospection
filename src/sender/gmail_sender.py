"""
Stage 5: Send approved emails via Gmail API using OAuth2.

Authentication:
- Uses credentials/credentials.json (Desktop App OAuth2 client secret)
- Caches token in credentials/token.json (auto-refreshed when expired)
- On first run, opens a browser window for OAuth2 consent
- Scopes: gmail.send + gmail.settings.basic (for fetching Gmail signature)

NOTE: If you had a token.json from a previous version, delete it and re-run
so Gmail re-authorises with the new gmail.settings.basic scope.

Scheduling:
- By default, emails are queued and sent on the next Monday at 09:00 local time
- Pass send_now=True (or --send-now CLI flag) to send immediately (useful for testing)
"""

import base64
import os
import re
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

import pytz
from apscheduler.schedulers.blocking import BlockingScheduler
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

# gmail.settings.basic is required to read the account signature via the API.
# If your existing token.json was created with only gmail.send, delete it so
# the OAuth flow re-runs and grants the new scope.
SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.settings.basic",
]

# Timezone used for the Monday 09:00 schedule
SEND_TIMEZONE = "Europe/Madrid"


# ---------------------------------------------------------------------------
# Gmail authentication
# ---------------------------------------------------------------------------

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
            flow.redirect_uri = "urn:ietf:wg:oauth:2.0:oob"
            auth_url, _ = flow.authorization_url(prompt="consent")
            print("\n" + "=" * 60)
            print("  Gmail OAuth2 — Abre esta URL en tu navegador:")
            print("=" * 60)
            print(f"\n  {auth_url}\n")
            print("=" * 60)
            auth_code = input("  Pega aquí el código de autorización: ").strip()
            flow.fetch_token(code=auth_code)
            creds = flow.credentials

        os.makedirs(os.path.dirname(TOKEN_PATH), exist_ok=True)
        with open(TOKEN_PATH, "w") as f:
            f.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)


# ---------------------------------------------------------------------------
# Gmail signature
# ---------------------------------------------------------------------------

def get_gmail_signature(service) -> str:
    """
    Fetch the HTML signature stored in Gmail settings for SENDER_EMAIL.
    Returns an empty string if the signature cannot be retrieved.
    """
    try:
        result = (
            service.users()
            .settings()
            .sendAs()
            .get(userId="me", sendAsEmail=SENDER_EMAIL)
            .execute()
        )
        sig = result.get("signature", "")
        if sig:
            print("  Gmail signature fetched successfully.")
        else:
            print("  Note: no signature found in Gmail settings for this address.")
        return sig
    except HttpError as exc:
        print(f"  Warning: could not fetch Gmail signature ({exc}). Continuing without it.")
        return ""


# ---------------------------------------------------------------------------
# MIME email construction
# ---------------------------------------------------------------------------

def _html_to_plain(html: str) -> str:
    """Strip HTML tags to produce a plain-text fallback."""
    text = re.sub(r"<[^>]+>", "", html)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def build_mime_email(company: dict, signature_html: str = "") -> str:
    """
    Build a base64url-encoded MIME email (multipart/alternative).

    If signature_html is provided (fetched from Gmail settings), it is
    injected into the HTML body immediately before </body> so it appears
    below the email content, matching how Gmail renders composed messages.
    """
    body = company.get("email_body", "")

    if signature_html:
        sig_block = (
            '<div style="margin-top:24px; padding-top:16px; '
            'border-top:1px solid #e0e0e0;">'
            f"{signature_html}"
            "</div>"
        )
        body = body.replace("</body>", f"{sig_block}\n</body>")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = company["email_subject"]
    msg["From"] = f"{SENDER_NAME} <{SENDER_EMAIL}>"
    msg["To"] = company["stakeholder_email"]

    plain = _html_to_plain(body)
    msg.attach(MIMEText(plain, "plain", "utf-8"))
    msg.attach(MIMEText(body, "html", "utf-8"))

    return base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")


# ---------------------------------------------------------------------------
# Scheduling
# ---------------------------------------------------------------------------

def _next_monday_at_9am() -> datetime:
    """
    Return the next Monday at 09:00 in SEND_TIMEZONE.

    - If today is Monday and it is before 09:00 → today at 09:00
    - Otherwise → next Monday at 09:00
    """
    tz = pytz.timezone(SEND_TIMEZONE)
    now = datetime.now(tz)
    days_until_monday = (7 - now.weekday()) % 7  # 0 = already Monday
    if days_until_monday == 0 and now.hour >= 9:
        days_until_monday = 7  # Already past 9 AM Monday → next week
    target_date = now + timedelta(days=days_until_monday)
    return target_date.replace(hour=9, minute=0, second=0, microsecond=0)


def _parse_schedule_at(value: str) -> datetime:
    """
    Parse a user-supplied schedule string into a timezone-aware datetime.

    Accepted formats:
      "tomorrow"           → tomorrow at 09:00 in SEND_TIMEZONE
      "YYYY-MM-DD"         → that date at 09:00 in SEND_TIMEZONE
      "YYYY-MM-DD HH:MM"   → exact datetime in SEND_TIMEZONE
    """
    tz = pytz.timezone(SEND_TIMEZONE)
    now = datetime.now(tz)

    if value.strip().lower() == "tomorrow":
        target = (now + timedelta(days=1)).replace(
            hour=9, minute=0, second=0, microsecond=0
        )
        return target

    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            naive = datetime.strptime(value.strip(), fmt)
            if fmt == "%Y-%m-%d":
                naive = naive.replace(hour=9, minute=0)
            return tz.localize(naive)
        except ValueError:
            continue

    raise ValueError(
        f"Cannot parse --schedule-at value: {value!r}\n"
        "Use 'tomorrow', 'YYYY-MM-DD', or 'YYYY-MM-DD HH:MM'"
    )


# ---------------------------------------------------------------------------
# Core send logic
# ---------------------------------------------------------------------------

def _do_send(review_file: str) -> None:
    """
    Authenticate, fetch signature, and dispatch all approved emails.
    Called directly (send_now=True) or by the scheduler.
    """
    from src.reviewer.review import load_approved_from_review

    approved = load_approved_from_review(review_file)
    if not approved:
        print("  [INFO] No approved emails found in the review file.")
        return

    print(f"\n  Sending {len(approved)} approved email(s)…")
    service = get_gmail_service()
    signature_html = get_gmail_signature(service)
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
            raw = build_mime_email(company, signature_html)
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


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def send_approved_emails(
    review_file: str,
    send_now: bool = False,
    schedule_at: Optional[str] = None,
) -> None:
    """
    Entry point for Stage 5.

    send_now=True        → send immediately (testing)
    schedule_at="..."    → schedule for a specific time: "tomorrow",
                           "YYYY-MM-DD", or "YYYY-MM-DD HH:MM"
    (default)            → schedule for next Monday at 09:00
    """
    if send_now:
        _do_send(review_file)
        return

    if schedule_at:
        try:
            target = _parse_schedule_at(schedule_at)
        except ValueError as e:
            print(f"  [ERROR] {e}")
            return
    else:
        target = _next_monday_at_9am()

    tz_label = target.strftime("%Z")
    print(
        f"\n  Emails scheduled for: "
        f"{target.strftime('%A %d %B %Y at %H:%M')} {tz_label}"
    )
    print("  Keep this terminal open. Press Ctrl+C to cancel.\n")

    scheduler = BlockingScheduler(timezone=SEND_TIMEZONE)
    scheduler.add_job(_do_send, "date", run_date=target, args=[review_file])

    try:
        scheduler.start()
    except KeyboardInterrupt:
        print("\n  Scheduling cancelled.")
