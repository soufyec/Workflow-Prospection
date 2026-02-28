import csv
import os
from datetime import datetime


def load_sent_emails(sent_log_path: str) -> set:
    """Return set of website URLs already successfully emailed."""
    sent = set()
    if not os.path.exists(sent_log_path):
        return sent
    with open(sent_log_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("status") == "sent":
                sent.add(row.get("website", "").strip().lower())
    return sent


def filter_new_companies(companies: list, sent_log_path: str) -> list:
    """Remove companies already present in the sent log."""
    already_sent = load_sent_emails(sent_log_path)
    return [
        c for c in companies
        if c.get("website", "").strip().lower() not in already_sent
    ]


def append_sent_log(sent_log_path: str, records: list) -> None:
    """Append sent records to CSV audit log. Creates file with headers if missing."""
    fieldnames = [
        "timestamp", "company_name", "website",
        "stakeholder_email", "stakeholder_name", "status", "error",
    ]
    os.makedirs(os.path.dirname(sent_log_path), exist_ok=True)
    file_exists = os.path.exists(sent_log_path)
    with open(sent_log_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        if not file_exists:
            writer.writeheader()
        writer.writerows(records)
