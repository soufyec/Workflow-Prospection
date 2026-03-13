"""
Dõlmen Studios — Prospection Email Automation

Usage:
  # ── Dõlmen mode (geo+sector, brand audit, Notion CRM) ──────────────────────
  python main.py --step dolmen                   # Full Dõlmen pipeline for today's city
  python main.py --step dolmen --city stockholm  # Force a specific city

  # ── VC-portfolio mode (original) ───────────────────────────────────────────
  python main.py --step discover    # Scrape VC portfolios + LinkedIn posts + Apollo
  python main.py --step apollo      # Browser-scrape Apollo people for all pipeline companies
  python main.py --step enrich      # Find stakeholder emails (scraping + multi-provider APIs)
  python main.py --step re-enrich   # Re-run APIs on companies with missing/weak emails
  python main.py --step generate    # Render personalized emails from template
  python main.py --step draft       # Create Gmail Drafts for ALL generated emails
  python main.py --step followup    # Check replies & create follow-up drafts (FU1/FU2/FU3)
  python main.py --step review      # (Legacy) Generate review CSV + HTML preview
  python main.py --step send        # Schedule approved emails for next Monday 09:00
  python main.py                    # Run full pipeline: discover → enrich → generate → draft
  python main.py --step all         # Same as above

  python main.py --status          # Show current pipeline state counts
  python main.py --reset           # Clear pipeline state (start fresh)

  # After reviewing drafts in Gmail, send them all:
  python update_drafts.py --send-drafts

  # Follow-up timing (days after previous email, configurable via .env):
  FOLLOWUP_1_DAYS=5    # First follow-up
  FOLLOWUP_2_DAYS=12   # Second follow-up
  FOLLOWUP_3_DAYS=21   # Final goodbye

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


def cmd_apollo(args):
    print("\n=== APOLLO BROWSER SCRAPER ===")
    from src.scraper.apollo_people_scraper import run_apollo_scraper
    from urllib.parse import urlparse

    state = load_state(PIPELINE_PATH)
    companies = state.get("discovered", []) or state.get("enriched", [])
    domains = []
    for c in companies:
        w = c.get("website", "")
        if w:
            d = urlparse(w).netloc.replace("www.", "")
            if d and d not in domains:
                domains.append(d)

    if not domains:
        print("  No hay empresas en el pipeline. Ejecuta --step dolmen primero.")
        return

    print(f"  Scraping Apollo para {len(domains)} dominios...")
    run_apollo_scraper(domains)


def cmd_enrich(args):
    print("\n=== STAGE 2: ENRICH (finding stakeholder emails) ===")
    from src.scraper.email_scraper import enrich_companies

    enriched = enrich_companies()
    print(f"\n  Total enriched: {len(enriched)}")


def cmd_reenrich(args):
    """
    Re-run multi-provider API waterfall on companies that already went through
    Stage 2 but still have no email, a pattern-based email, or a generic one.
    Also verifies emails in the already-generated list.
    """
    print("\n=== RE-ENRICH: Multi-provider API pass ===")
    from src.scraper.email_scraper import score_email
    from src.enricher.multi_provider import enrich_email, verify_existing_email
    from src.utils.pipeline_state import load_state, save_state

    state = load_state(PIPELINE_PATH)
    enriched = state.get("enriched", [])

    # Targets: no email, pattern-based, or generic (score ≥ 8)
    targets = [
        c for c in enriched
        if (
            not c.get("stakeholder_email")
            or c.get("email_source_url") == "pattern-based"
            or score_email(c.get("stakeholder_email") or "", "") >= 8
        )
    ]

    print(f"  Companies to re-enrich: {len(targets)} / {len(enriched)}")
    if not targets:
        print("  Nothing to do.")
        return

    upgraded = 0
    for company in targets:
        name = company.get("company_name", "?")
        old_email = company.get("stakeholder_email") or "—"
        print(f"  [RE-ENRICH] {name}  (current: {old_email})")
        api_result = enrich_email(company)
        if api_result.get("stakeholder_email"):
            company.update(api_result)
            company["email_source_url"] = f"api:{api_result['email_provider']}"
            print(f"    → {api_result['stakeholder_email']}"
                  f" ({api_result['email_provider']},"
                  f" confidence: {api_result.get('email_confidence', '?')},"
                  f" verified: {api_result.get('email_verified', '?')})")
            upgraded += 1
        else:
            print("    → no result from any provider")

    # Also verify existing scraped emails that haven't been verified yet
    unverified = [
        c for c in enriched
        if c.get("stakeholder_email")
        and c.get("email_verified") is None
        and c not in targets
    ]
    print(f"\n  Verifying {len(unverified)} previously scraped email(s)...")
    invalidated = 0
    for company in unverified:
        verification = verify_existing_email(company)
        company.update(verification)
        if not verification.get("email_verified", True):
            invalidated += 1
            print(f"    [INVALID] {company.get('stakeholder_email')} ({company.get('company_name')})")

    state["enriched"] = enriched
    save_state(PIPELINE_PATH, state)
    print(f"\n  Done — upgraded: {upgraded} | invalidated: {invalidated}")


def cmd_generate(args):
    print("\n=== STAGE 3: GENERATE (personalizing emails) ===")
    from src.generator.email_generator import generate_emails

    generated = generate_emails()
    print(f"\n  Total emails ready for review: {len(generated)}")


def cmd_draft(args):
    print("\n=== STAGE 4: CREATE DRAFTS ===")
    from src.sender.gmail_sender import create_drafts_from_generated

    create_drafts_from_generated()


def cmd_review(args):
    print("\n=== STAGE 4 (legacy): REVIEW CSV ===")
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


def cmd_followup(args):
    print("\n=== STAGE 5: FOLLOW-UPS ===")
    from src.sender.followup_sender import run_followups

    run_followups()


def cmd_dolmen(args):
    """Full Dõlmen pipeline: geo discovery → brand audit → enrich → generate → Notion → drafts."""
    from src.dolmen_pipeline import run_dolmen_pipeline
    force_city = getattr(args, "city", None)
    run_dolmen_pipeline(force_city=force_city)


def cmd_all(args):
    cmd_discover(args)
    cmd_enrich(args)
    cmd_generate(args)
    cmd_draft(args)
    print("\n  Pipeline complete — all emails are now Gmail Drafts.")
    print("  Review them in Gmail, then send with:")
    print("  python update_drafts.py --send-drafts")


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

    mode_group = parser.add_mutually_exclusive_group(required=False)
    mode_group.add_argument(
        "--step",
        choices=["dolmen", "discover", "apollo", "enrich", "re-enrich", "generate", "draft", "followup", "review", "send", "all"],
        nargs="?",
        const="all",
        default=None,
        help="Which pipeline stage to run (default: all)",
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
        "--city",
        metavar="CITY",
        default=None,
        help=(
            "Override today's geographic market for --step dolmen. "
            "Options: london, berlin, amsterdam, stockholm, copenhagen, madrid, barcelona."
        ),
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
        "dolmen": cmd_dolmen,
        "discover": cmd_discover,
        "apollo": cmd_apollo,
        "enrich": cmd_enrich,
        "re-enrich": cmd_reenrich,
        "generate": cmd_generate,
        "draft": cmd_draft,
        "followup": cmd_followup,
        "review": cmd_review,
        "send": cmd_send,
        "all": cmd_all,
    }

    if args.status:
        cmd_status(args)
    elif args.reset:
        cmd_reset(args)
    else:
        step = args.step or "all"
        dispatch[step](args)


if __name__ == "__main__":
    main()
