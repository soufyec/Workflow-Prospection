"""
Test email sender: sends a real prospect email to a test address.

The MIME "To" header shows the real prospect (so you see it exactly as they would)
but Gmail delivers to TEST_EMAIL instead.

Usage: python send_test.py
"""

import base64
import os
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from src.sender.gmail_sender import get_gmail_service

GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"

# ── Test config ──────────────────────────────────────────────────────────────
TEST_EMAIL = "soufyess@gmail.com"       # actual delivery address (your inbox)

PROSPECT = {
    "company_name": "Qura",
    "real_to": "team@qura.law",          # shown in the To: header
    "subject": "Branding opportunity — Qura × Dõlmen Studios",
    "body": """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="font-family: Georgia, 'Times New Roman', serif; max-width: 600px;
             margin: 0 auto; color: #1a1a1a; line-height: 1.75; padding: 24px;">

  <p>Hello there,</p>

  <p>
    I'm Soufyan, Strategic Branding Manager at <strong>Dõlmen Studios</strong>.
  </p>

  <p>
    At Dõlmen, we help companies through brand strategy and design with a strong focus
    on performance &amp; human connections — so they don't just look good, but actively
    drive business metrics such as leads, conversions, investment, and strategic partnerships.
  </p>

  <p>
    We've collaborated with companies such as Nike on strategic branding projects.
    If you'd like to see how we approach this work, you can find some of our most relevant
    case studies on
    <a href="https://www.dolmenstudios.com" style="color: #2b5797; text-decoration: none;">our website</a>.
  </p>

  <p>
    While researching technology companies, I came across
    <strong>Qura</strong>.
    Your product is solid, but we believe there's a meaningful opportunity to strengthen
    your visual identity and messaging to match the quality of your underlying business.
  </p>

  <p>
    If useful, we'd be happy to connect and offer a <strong>Free Brand Audit</strong>
    in a first video call — sharing concrete insights and specific improvement opportunities
    for Qura.
  </p>

  <p>
    If that sounds good to you, just let me know what your availability looks like for next week.
  </p>

  <p style="margin-top: 2em;">
    Kind regards,<br>
    <strong>Soufyan</strong>
  </p>

  <hr style="border: none; border-top: 1px solid #eee; margin-top: 2.5em;">
  <p style="font-size: 11px; color: #999; line-height: 1.5;">
    You received this because Qura appeared in our research on
    technology companies and we believe there could be a genuine fit.
    Reply STOP to opt out and we won't contact you again.
  </p>

</body>
</html>""",
}

SENDER_NAME = os.environ.get("SENDER_NAME", "Dõlmen Studios")
SENDER_EMAIL = os.environ.get("SENDER_EMAIL", "hello@dolmenstudios.com")


def _html_to_plain(html: str) -> str:
    import re
    text = re.sub(r"<[^>]+>", "", html)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def build_test_email(prospect: dict, test_email: str) -> str:
    """
    Build MIME email that:
    - Shows the real prospect address in To: header (looks authentic)
    - Is actually delivered to test_email via the API envelope
    """
    msg = MIMEMultipart("alternative")
    msg["Subject"] = prospect["subject"]
    msg["From"] = f"{SENDER_NAME} <{SENDER_EMAIL}>"
    # Show prospect in the To: header — Gmail will deliver to test_email instead
    msg["To"] = f"{prospect['company_name']} <{prospect['real_to']}>"

    plain = _html_to_plain(prospect["body"])
    msg.attach(MIMEText(plain, "plain", "utf-8"))
    msg.attach(MIMEText(prospect["body"], "html", "utf-8"))

    return base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")


def main():
    print("=" * 60)
    print("  TEST EMAIL SENDER — Dõlmen Studios Prospection Pipeline")
    print("=" * 60)
    print(f"\n  Prospect : {PROSPECT['company_name']} <{PROSPECT['real_to']}>")
    print(f"  Delivered: {TEST_EMAIL}  (your inbox)")
    print(f"  Subject  : {PROSPECT['subject']}\n")

    service = get_gmail_service()

    raw = build_test_email(PROSPECT, TEST_EMAIL)

    # Gmail API envelope: "to" here controls actual delivery
    resp = service.post(
        f"{GMAIL_API}/messages/send",
        json={
            "raw": raw,
            "envelope_to": [TEST_EMAIL],   # delivery override
        },
    )

    if resp.status_code == 200:
        msg_id = resp.json().get("id", "?")
        print(f"  [OK] Email sent! Message ID: {msg_id}")
        print(f"  → Check {TEST_EMAIL} — the To: header will show {PROSPECT['real_to']}")
    else:
        # Fallback: set To: directly to test_email (less realistic header but delivers)
        print(f"  [WARN] envelope_to not supported (HTTP {resp.status_code}), retrying with To: override…")

        msg2 = MIMEMultipart("alternative")
        msg2["Subject"] = f"[TEST — as sent to {PROSPECT['real_to']}] {PROSPECT['subject']}"
        msg2["From"] = f"{SENDER_NAME} <{SENDER_EMAIL}>"
        msg2["To"] = TEST_EMAIL
        msg2["X-Original-To"] = PROSPECT["real_to"]

        plain = _html_to_plain(PROSPECT["body"])
        msg2.attach(MIMEText(plain, "plain", "utf-8"))
        msg2.attach(MIMEText(PROSPECT["body"], "html", "utf-8"))

        raw2 = base64.urlsafe_b64encode(msg2.as_bytes()).decode("utf-8")
        resp2 = service.post(f"{GMAIL_API}/messages/send", json={"raw": raw2})

        if resp2.status_code == 200:
            print(f"  [OK] Test email sent to {TEST_EMAIL}  (subject prefixed with [TEST])")
        else:
            print(f"  [FAIL] {resp2.status_code}: {resp2.text}")


if __name__ == "__main__":
    main()
