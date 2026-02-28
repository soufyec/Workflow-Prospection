# Dõlmen Studios — Prospection Email Automation

Automated pipeline to find recently-funded startups in Netherlands + Benelux,
discover stakeholder emails, and send personalized brand partnership outreach
via Gmail — with a manual review step before anything is sent.

---

## How It Works

```
VC Portfolio Pages ─┐
                    ├──► Discover companies ──► Find emails ──► Generate emails ──► Review ──► Send
VC LinkedIn Posts  ─┘
```

**5 pipeline stages:**

| Step | Command | What it does |
|------|---------|--------------|
| 1 | `--step discover` | Scrapes VC portfolio pages + LinkedIn posts for funded companies |
| 2 | `--step enrich` | Scrapes each company site for CEO/CMO/marketing email |
| 3 | `--step generate` | Renders personalized email from Jinja2 template |
| 4 | `--step review` | Outputs a CSV + HTML preview for your approval |
| 5 | `--step send` | Sends only the rows you marked "approved" via Gmail API |

---

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
playwright install chromium
```

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env`:
```dotenv
SENDER_EMAIL=hello@dolmenstudios.com
SENDER_NAME=Dõlmen Studios
LINKEDIN_EMAIL=your@email.com
LINKEDIN_PASSWORD=yourpassword
```

### 3. Set up Gmail API (one-time)

See [Gmail OAuth2 Setup](#gmail-oauth2-setup) below.

### 4. Customize the email template

Edit `templates/prospection_email.html` with your actual strategy and messaging
from the Google Drive doc. The template uses Jinja2 variables:

| Variable | Value |
|----------|-------|
| `{{ recipient_name }}` | Stakeholder first name (or "there") |
| `{{ company_name }}` | Target company |
| `{{ vc_source }}` | VC that backed them (social proof) |
| `{{ funding_stage }}` | pre-seed / seed / Series A |
| `{{ industry_hook }}` | Dynamic one-liner for their sector |
| `{{ sender_name }}` | Dõlmen Studios |
| `{{ sender_email }}` | Your email |

### 5. Run the pipeline

```bash
# Full run (stops before sending for your review)
python main.py --step all

# Then open review/review_YYYYMMDD_HHMMSS.html in your browser
# Open the matching .csv, set review_status = "approved" for the rows to send
# Save the CSV, then:
python main.py --step send
```

---

## Commands Reference

```bash
python main.py --step discover   # Scrape VCs + LinkedIn posts
python main.py --step enrich     # Find stakeholder emails
python main.py --step generate   # Render personalized emails
python main.py --step review     # Generate review CSV + HTML
python main.py --step send       # Send approved emails
python main.py --step all        # Run stages 1–4 in sequence

python main.py --status          # Show record counts at each stage
python main.py --reset           # Clear pipeline state (keeps sent log)

# Send from a specific review file
python main.py --step send --review-file review/review_20240301_120000.csv
```

---

## Gmail OAuth2 Setup

### Step 1: Create a Google Cloud Project
1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Create a new project (e.g. "Dolmen Prospection")
3. Go to **APIs & Services → Library**
4. Search for **Gmail API** and click **Enable**

### Step 2: Configure OAuth Consent Screen
1. Go to **APIs & Services → OAuth Consent Screen**
2. Choose **External** (for personal Gmail) or **Internal** (Google Workspace)
3. Fill in:
   - App name: `Dõlmen Prospection`
   - Support email: your email
   - Developer contact email: your email
4. Under **Scopes**, add: `https://www.googleapis.com/auth/gmail.send`
5. Under **Test users**, add your own Gmail address
6. Save and continue

### Step 3: Create OAuth2 Credentials
1. Go to **APIs & Services → Credentials**
2. Click **Create Credentials → OAuth client ID**
3. Application type: **Desktop app** ← critical, not Web application
4. Name: `Prospection CLI`
5. Click **Create**
6. Download the JSON file
7. Save it as `credentials/credentials.json`

### Step 4: First-Time Authentication
Run any send command:
```bash
python main.py --step send
```
A browser window will open automatically. Log in with your Gmail account and
approve the requested permission (`gmail.send`). The token is saved to
`credentials/token.json` and reused on all subsequent runs.

---

## Adding More VCs

Edit `config/vc_list.json`. Each entry:

```json
{
  "name": "VC Name",
  "url": "https://vcwebsite.com",
  "portfolio_url": "https://vcwebsite.com/portfolio",
  "linkedin_url": "https://www.linkedin.com/company/vc-slug/posts/",
  "scrape_linkedin": true,
  "typical_stage": "seed",
  "focus_sector": "saas b2b",
  "scrape_strategy": "static",
  "company_selector": null
}
```

- `scrape_strategy`: `"static"` for most sites, `"js"` for React/Vue portfolio pages
- `company_selector`: optional CSS selector for portfolio cards (e.g. `".portfolio-item"`)
- `scrape_linkedin`: set `false` to skip LinkedIn for that VC

---

## Data Files (gitignored)

| File | Description |
|------|-------------|
| `data/pipeline.json` | Current pipeline state (all stages) |
| `data/sent_log.csv` | Audit trail of all sent emails |
| `review/review_*.csv` | Review files (edit to approve/reject) |
| `review/review_*.html` | HTML email previews |
| `credentials/credentials.json` | Gmail OAuth2 client secret |
| `credentials/token.json` | Gmail OAuth2 token cache |
| `credentials/linkedin_session.json` | LinkedIn session cookies |

---

## Notes

- **Email coverage**: Scraping typically finds emails for 40–60% of companies.
  Companies without a discovered email are excluded from the generate step and
  visible in the review CSV with a blank stakeholder_email column.
- **LinkedIn scraping**: The LinkedIn scraper opens a visible browser window
  (not headless) so you can handle any CAPTCHA or 2FA prompts manually.
  If triggered, the tool will pause and wait for you to press Enter.
- **Deduplication**: Companies already in `data/sent_log.csv` are automatically
  filtered out on subsequent discover runs — you won't email the same company twice.
- **Rate limiting**: The scraper uses random delays (1.5–4s between requests)
  and rotates user-agents to minimize bot detection.
