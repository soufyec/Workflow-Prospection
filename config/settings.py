import os
from dotenv import load_dotenv

load_dotenv()

SENDER_EMAIL = os.environ.get("SENDER_EMAIL", "hello@dolmenstudios.com")
SENDER_NAME = os.environ.get("SENDER_NAME", "Dõlmen Studios")

LINKEDIN_EMAIL = os.environ.get("LINKEDIN_EMAIL", "")
LINKEDIN_PASSWORD = os.environ.get("LINKEDIN_PASSWORD", "")

CREDENTIALS_PATH = os.environ.get("CREDENTIALS_PATH", "credentials/credentials.json")
TOKEN_PATH = os.environ.get("TOKEN_PATH", "credentials/token.json")
LINKEDIN_SESSION_PATH = os.environ.get("LINKEDIN_SESSION_PATH", "credentials/linkedin_session.json")
PIPELINE_PATH = os.environ.get("PIPELINE_PATH", "data/pipeline.json")
SENT_LOG_PATH = os.environ.get("SENT_LOG_PATH", "data/sent_log.csv")
REVIEW_DIR = os.environ.get("REVIEW_DIR", "review/")
TEMPLATE_PATH = os.environ.get("TEMPLATE_PATH", "templates/prospection_email.html")
VC_LIST_PATH = os.environ.get("VC_LIST_PATH", "config/vc_list.json")

REQUEST_DELAY_MIN = float(os.environ.get("REQUEST_DELAY_MIN", "1.5"))
REQUEST_DELAY_MAX = float(os.environ.get("REQUEST_DELAY_MAX", "4.0"))
MAX_RETRIES = int(os.environ.get("MAX_RETRIES", "3"))
