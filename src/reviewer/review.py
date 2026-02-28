"""
Stage 4: Generate a review CSV + HTML preview for human approval.

Workflow:
1. Run: python main.py --step review
2. Open review/review_YYYYMMDD_HHMMSS.html in browser to preview emails
3. Open review/review_YYYYMMDD_HHMMSS.csv in Excel / Google Sheets
4. Set review_status = "approved" for emails you want to send
5. Save the CSV
6. Run: python main.py --step send
"""

import csv
import os
from datetime import datetime
from html import escape

from config.settings import PIPELINE_PATH, REVIEW_DIR
from src.utils.pipeline_state import load_state, save_state

REVIEW_COLUMNS = [
    "review_status",       # USER EDITS THIS: "approved" | "rejected" | "pending"
    "company_name",
    "website",
    "vc_source",
    "funding_stage",
    "industry",
    "discovery_source",
    "post_text",
    "stakeholder_name",
    "stakeholder_email",
    "email_source_url",
    "email_subject",
    "email_body",
]


def generate_review_csv() -> str:
    """
    Write a timestamped review CSV + HTML preview to the review/ directory.
    Returns the path to the generated CSV.
    """
    state = load_state(PIPELINE_PATH)
    generated = state.get("generated", [])

    if not generated:
        print("  [WARN] No generated emails found. Run --step generate first.")
        return ""

    os.makedirs(REVIEW_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = os.path.join(REVIEW_DIR, f"review_{timestamp}.csv")
    html_path = os.path.join(REVIEW_DIR, f"review_{timestamp}.html")

    # Write CSV
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=REVIEW_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(generated)

    # Write HTML preview
    _write_html_preview(generated, html_path, csv_path)

    state["review_file"] = csv_path
    save_state(PIPELINE_PATH, state)

    print(f"\n  Review CSV:      {csv_path}")
    print(f"  HTML preview:    {html_path}")
    print(f"\n  NEXT STEPS:")
    print(f"  1. Open the HTML file in your browser to preview each email")
    print(f"  2. Open the CSV, set 'review_status' to 'approved' for emails to send")
    print(f"  3. Save the CSV")
    print(f"  4. Run: python main.py --step send")
    return csv_path


def _write_html_preview(companies: list, html_path: str, csv_path: str) -> None:
    """Generate a standalone HTML review page with expandable email previews."""
    rows_html = ""
    for c in companies:
        status = c.get("review_status", "pending")
        bg = {"approved": "#d4edda", "rejected": "#f8d7da"}.get(status, "#f8f9fa")
        source_badge = (
            '<span style="background:#d1ecf1;padding:2px 6px;border-radius:3px;'
            'font-size:11px">LinkedIn</span>'
            if c.get("discovery_source") == "linkedin_post"
            else '<span style="background:#e2e3e5;padding:2px 6px;border-radius:3px;'
            'font-size:11px">Portfolio</span>'
        )
        post_text = escape(c.get("post_text", "") or "")
        post_cell = (
            f'<details><summary style="cursor:pointer;color:#666;font-size:11px">'
            f'View post snippet</summary>'
            f'<div style="font-size:11px;color:#555;margin-top:4px">{post_text}</div>'
            f"</details>"
            if post_text
            else ""
        )

        rows_html += f"""
        <tr style="background:{bg}">
          <td style="font-weight:bold">{escape(c.get('company_name', ''))}</td>
          <td>{escape(c.get('funding_stage', ''))}</td>
          <td>{source_badge}{post_cell}</td>
          <td>{escape(c.get('vc_source', ''))}</td>
          <td>{escape(c.get('stakeholder_name', '') or '')}</td>
          <td>{escape(c.get('stakeholder_email', '') or '')}</td>
          <td style="font-weight:bold;color:{'#28a745' if status=='approved' else '#dc3545' if status=='rejected' else '#6c757d'}">{status}</td>
          <td>
            <details>
              <summary style="cursor:pointer;color:#2b5797">{escape(c.get('email_subject', ''))}</summary>
              <div style="padding:12px;border:1px solid #dee2e6;margin-top:6px;border-radius:4px;background:#fff">
                {c.get('email_body', '')}
              </div>
            </details>
          </td>
        </tr>"""

    approved_count = sum(1 for c in companies if c.get("review_status") == "approved")
    pending_count = sum(1 for c in companies if c.get("review_status", "pending") == "pending")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Dõlmen Studios — Prospection Review</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif;
           padding: 24px; color: #212529; }}
    h1 {{ color: #1a1a2e; }}
    .meta {{ background: #f8f9fa; padding: 12px 16px; border-radius: 6px;
            margin-bottom: 20px; font-size: 14px; line-height: 1.8; }}
    .stats {{ display: flex; gap: 16px; margin-bottom: 20px; }}
    .stat {{ background: #e9ecef; padding: 8px 16px; border-radius: 6px;
            font-size: 13px; font-weight: 600; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
    th, td {{ border: 1px solid #dee2e6; padding: 8px 10px;
              text-align: left; vertical-align: top; }}
    th {{ background: #1a1a2e; color: #fff; position: sticky; top: 0; }}
    tr:hover {{ filter: brightness(0.97); }}
  </style>
</head>
<body>
  <h1>Dõlmen Studios — Prospection Email Review</h1>
  <div class="meta">
    <strong>Review CSV:</strong> <code>{csv_path}</code><br>
    <strong>Instructions:</strong>
    Open the CSV, set <code>review_status</code> to <strong>approved</strong>
    for the emails you want to send, save, then run:
    <code>python main.py --step send</code>
  </div>
  <div class="stats">
    <div class="stat">Total: {len(companies)}</div>
    <div class="stat" style="background:#d4edda">Approved: {approved_count}</div>
    <div class="stat" style="background:#f8f9fa">Pending: {pending_count}</div>
  </div>
  <table>
    <thead>
      <tr>
        <th>Company</th>
        <th>Stage</th>
        <th>Source</th>
        <th>VC</th>
        <th>Stakeholder</th>
        <th>Email</th>
        <th>Status</th>
        <th>Draft Email (click to expand)</th>
      </tr>
    </thead>
    <tbody>{rows_html}</tbody>
  </table>
</body>
</html>"""

    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)


def load_approved_from_review(review_file: str) -> list:
    """Read the edited review CSV and return only rows where review_status == 'approved'."""
    if not os.path.exists(review_file):
        print(f"  [ERROR] Review file not found: {review_file}")
        return []

    approved = []
    with open(review_file, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("review_status", "").strip().lower() == "approved":
                approved.append(dict(row))
    return approved
