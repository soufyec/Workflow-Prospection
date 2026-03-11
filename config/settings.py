import os
from dotenv import load_dotenv

load_dotenv()

SENDER_EMAIL = os.environ.get("SENDER_EMAIL", "hello@dolmenstudios.com")
SENDER_NAME = os.environ.get("SENDER_NAME", "Dõlmen Studios")

# ── Email discovery APIs ──────────────────────────────────────────────────────
# Apollo.io — https://developer.apollo.io/
APOLLO_API_KEY = os.environ.get("APOLLO_API_KEY", "")

# FindyMail — https://app.findymail.com/  (find + verify)
FINDYMAIL_API_KEY = os.environ.get("FINDYMAIL_API_KEY", "")

# Prospeo — https://prospeo.io/  (domain search + email finder)
PROSPEO_API_KEY = os.environ.get("PROSPEO_API_KEY", "")

# IcyPeas — https://icypeas.com/  (email search by name + domain)
ICYPEAS_API_KEY = os.environ.get("ICYPEAS_API_KEY", "")

# LeadMagic — https://leadmagic.io/  (email finder by name + domain)
LEADMAGIC_API_KEY = os.environ.get("LEADMAGIC_API_KEY", "")

# Wiza — https://wiza.co/  (LinkedIn URL → email)
WIZA_API_KEY = os.environ.get("WIZA_API_KEY", "")

# ContactOut — https://contactout.com/  (LinkedIn URL → email)
CONTACTOUT_API_KEY = os.environ.get("CONTACTOUT_API_KEY", "")

GMAIL_SIGNATURE = os.environ.get("GMAIL_SIGNATURE", "")

LINKEDIN_EMAIL = os.environ.get("LINKEDIN_EMAIL", "")
LINKEDIN_PASSWORD = os.environ.get("LINKEDIN_PASSWORD", "")

CREDENTIALS_PATH = os.environ.get("CREDENTIALS_PATH", "credentials/credentials.json")
TOKEN_PATH = os.environ.get("TOKEN_PATH", "credentials/token.json")
LINKEDIN_SESSION_PATH = os.environ.get("LINKEDIN_SESSION_PATH", "credentials/linkedin_session.json")
PIPELINE_PATH = os.environ.get("PIPELINE_PATH", "data/pipeline.json")
SENT_LOG_PATH = os.environ.get("SENT_LOG_PATH", "data/sent_log.csv")
FOLLOWUP_LOG_PATH = os.environ.get("FOLLOWUP_LOG_PATH", "data/followup_log.csv")
REVIEW_DIR = os.environ.get("REVIEW_DIR", "review/")
TEMPLATE_PATH = os.environ.get("TEMPLATE_PATH", "templates/prospection_email.html")
FOLLOWUP_1_TEMPLATE_PATH = os.environ.get("FOLLOWUP_1_TEMPLATE_PATH", "templates/followup_1.html")
FOLLOWUP_2_TEMPLATE_PATH = os.environ.get("FOLLOWUP_2_TEMPLATE_PATH", "templates/followup_2.html")
FOLLOWUP_3_TEMPLATE_PATH = os.environ.get("FOLLOWUP_3_TEMPLATE_PATH", "templates/followup_3.html")
VC_LIST_PATH = os.environ.get("VC_LIST_PATH", "config/vc_list.json")

# Days after initial email to send each follow-up
FOLLOWUP_1_DAYS = int(os.environ.get("FOLLOWUP_1_DAYS", "5"))
FOLLOWUP_2_DAYS = int(os.environ.get("FOLLOWUP_2_DAYS", "12"))
FOLLOWUP_3_DAYS = int(os.environ.get("FOLLOWUP_3_DAYS", "21"))

REQUEST_DELAY_MIN = float(os.environ.get("REQUEST_DELAY_MIN", "1.5"))
REQUEST_DELAY_MAX = float(os.environ.get("REQUEST_DELAY_MAX", "4.0"))
MAX_RETRIES = int(os.environ.get("MAX_RETRIES", "3"))

