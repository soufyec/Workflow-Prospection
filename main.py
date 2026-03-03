"""
Dõlmen Studios — Prospection Email Automation

Usage:
  python main.py --step discover   # Scrape VC portfolios + LinkedIn posts + Apollo
  python main.py --step enrich     # Find stakeholder emails on company websites
  python main.py --step generate   # Render personalized emails from template
  python main.py --step review     # Generate review CSV + HTML preview
  python main.py --step send       # Schedule approved emails for next Monday 09:00
  python main.py --step all        # Run discover → enrich → generate → review

  python main.py --status          # Show current pipeline state counts
  python main.py --reset           # Clear pipeline state (start fresh)

  # Optional flags for --step send:
  python main.py --step send --review-file review/review_20260302_corrected.csv
  python main.py --step send --send-now              # Send immediately (testing)
  python main.py --step send --schedule-at tomorrow  # Tomorrow at 09:00
  python main.py --step send --schedule-at "2026-03-10"        # That date at 09:00
  python main.py --step send --schedule-at "2026-03-10 14:30"  # Exact time
  python main.py --step send --create-drafts         # Save as Gmail Drafts (review before sending)
"""

import argparse
import sys

from config.settings import PIPELINE_PATH, SENT_LOG_PATH
from src.utils.pipeline_state import load_state, save_state


def cmd_discover(args):
    print("\n=== STAGE 1: DISCOVER ===")
    from src.scraper.vc_scraper import discover_companies

    companies = discover_companies()
    print(f"\n  New companies to process: {len(companies)}")


def cmd_enrich(args):
    print("\n=== STAGE 2: ENRICH (finding stakeholder emails) ===")
    from src.scraper.email_scraper import enrich_companies

    enriched = enrich_companies()
    print(f"\n  Total enriched: {len(enriched)}")


def cmd_generate(args):
    print("\n=== STAGE 3: GENERATE (personalizing emails) ===")
    from src.generator.email_generator import generate_emails

    generated = generate_emails()
    print(f"\n  Total emails ready for review: {len(generated)}")


def cmd_review(args):
    print("\n=== STAGE 4: REVIEW ===")
    from src.reviewer.review import generate_review_csv

    generate_review_csv()


def cmd_send(args):
    print("\n=== STAGE 5: SEND ===")
    state = load_state(PIPELINE_PATH)
    review_file = getattr(args, "review_file", None) or state.get("review_file")

    if not review_file:
        print("  [ERROR] No review file found.")
        print("  Run --step review first, or pass --review-file <path>")
        sys.exit(1)

    create_drafts = getattr(args, "create_drafts", False)

    if create_drafts:
        from src.sender.gmail_sender import create_drafts_from_review
        create_drafts_from_review(review_file)
        return

    send_now = getattr(args, "send_now", False)
    schedule_at = getattr(args, "schedule_at", None)
    from src.sender.gmail_sender import send_approved_emails

    send_approved_emails(review_file, send_now=send_now, schedule_at=schedule_at)


def cmd_all(args):
    cmd_discover(args)
    cmd_enrich(args)
    cmd_generate(args)
    cmd_review(args)
    print("\n  Pipeline complete through review stage.")
    print("  Edit the review CSV, approve rows, then run:")
    print("  python main.py --step send")


def cmd_status(args):
    state = load_state(PIPELINE_PATH)
    print("\n=== PIPELINE STATUS ===")
    for key in ["discovered", "enriched", "generated", "approved", "sent"]:
        items = state.get(key, [])
        print(f"  {key:12s}: {len(items):4d} records")
    review_file = state.get("review_file")
    print(f"  review_file: {review_file or 'not generated yet'}")
    print()


def cmd_reset(args):
    confirm = input(
        "  This will clear data/pipeline.json. "
        "Sent emails are preserved in data/sent_log.csv.\n"
        "  Type 'yes' to confirm: "
    ).strip().lower()
    if confirm == "yes":
        save_state(
            PIPELINE_PATH,
            {
                "discovered": [],
                "enriched": [],
                "generated": [],
                "review_file": None,
                "approved": [],
                "sent": [],
            },
        )
        print("  Pipeline state reset.")
    else:
        print("  Aborted.")


def main():
    parser = argparse.ArgumentParser(
        description="Dõlmen Studios — Prospection Email Automation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument(
        "--step",
        choices=["discover", "enrich", "generate", "review", "send", "all"],
        help="Which pipeline stage to run",
    )
    mode_group.add_argument(
        "--status",
        action="store_true",
        help="Show current pipeline state summary",
    )
    mode_group.add_argument(
        "--reset",
        action="store_true",
        help="Reset pipeline state (does not delete sent_log.csv)",
    )

    parser.add_argument(
        "--review-file",
        metavar="PATH",
        help="Path to the edited review CSV (used with --step send)",
    )
    parser.add_argument(
        "--send-now",
        action="store_true",
        default=False,
        help="Send immediately instead of scheduling (testing only)",
    )
    parser.add_argument(
        "--schedule-at",
        metavar="WHEN",
        default=None,
        help=(
            "Schedule send for a specific time. "
            "Values: 'tomorrow', 'YYYY-MM-DD', 'YYYY-MM-DD HH:MM'. "
            "Default (omitted): next Monday at 09:00."
        ),
    )
    parser.add_argument(
        "--create-drafts",
        action="store_true",
        default=False,
        dest="create_drafts",
        help="Save approved emails as Gmail Drafts instead of sending. "
             "Review and send them manually from your Gmail Drafts folder.",
    )

    args = parser.parse_args()

    dispatch = {
        "discover": cmd_discover,
        "enrich": cmd_enrich,
        "generate": cmd_generate,
        "review": cmd_review,
        "send": cmd_send,
        "all": cmd_all,
    }

    if args.status:
        cmd_status(args)
    elif args.reset:
        cmd_reset(args)
    else:
        dispatch[args.step](args)


if __name__ == "__main__":
    main()
