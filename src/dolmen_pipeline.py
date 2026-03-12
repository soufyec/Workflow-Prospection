"""
Dõlmen Studios — Full Prospection Pipeline (Dõlmen mode).

Flow:
  1. DISCOVER  — Apollo geo+sector search for today's city rotation (10–150 employees)
  2. AUDIT     — 5-criteria brand checklist; discard leads with score < 3
  3. ENRICH    — Find stakeholder emails (web scraping + multi-provider API waterfall)
  4. GENERATE  — Render personalized outreach emails with brand_problem as pain point
  5. NOTION    — Push qualified leads to the Dõlmen Studios CRM (Notion)
  6. DRAFTS    — Create Gmail Drafts for review before sending

Usage (via main.py):
  python main.py --step dolmen
  python main.py --step dolmen --city stockholm   # Force city override
"""

from config.settings import PIPELINE_PATH
from src.utils.pipeline_state import load_state, save_state


def run_dolmen_pipeline(force_city=None):
    """
    Orchestrate the full Dõlmen prospection run for today's geographic market.

    Args:
        force_city: Optional city override (london, berlin, amsterdam, stockholm, madrid).
    """
    print("\n" + "=" * 60)
    print("  DÕLMEN STUDIOS — PROSPECTION PIPELINE")
    print("=" * 60)

    # ── Stage 1: DISCOVER ────────────────────────────────────────
    print("\n=== STAGE 1: DISCOVER (Apollo geo search) ===")
    from src.scraper.dolmen_discovery import discover_dolmen_leads
    discovered = discover_dolmen_leads(force_city=force_city)
    if not discovered:
        print("  No companies found — check APOLLO_API_KEY and try again.")
        return

    # Merge into pipeline state (discovered key)
    state = load_state(PIPELINE_PATH)
    existing_websites = {c["website"] for c in state.get("discovered", [])}
    new_companies = [c for c in discovered if c["website"] not in existing_websites]
    state["discovered"] = state.get("discovered", []) + new_companies
    save_state(PIPELINE_PATH, state)
    print(f"\n  → {len(new_companies)} new companies added to pipeline.")

    # ── Stage 2: BRAND AUDIT ─────────────────────────────────────
    print("\n=== STAGE 2: BRAND AUDIT (5-criteria checklist) ===")
    from src.auditor.brand_auditor import run_brand_audit
    # Only audit newly discovered companies that don't yet have a checklist_score
    to_audit = [c for c in new_companies if c.get("checklist_score") is None]
    kept, discarded_count = run_brand_audit(to_audit, min_score=3)

    # Update pipeline state with audit results (for all, including discarded)
    audited_by_url = {c["website"]: c for c in to_audit}
    for company in kept:
        audited_by_url[company["website"]] = company

    # Rebuild discovered list with audit data, removing sub-threshold companies
    kept_websites = {c["website"] for c in kept}
    state = load_state(PIPELINE_PATH)
    # Keep previously audited + newly kept; remove newly discarded
    new_websites = {c["website"] for c in new_companies}
    state["discovered"] = [
        audited_by_url.get(c["website"], c)
        for c in state["discovered"]
        if c["website"] not in new_websites or c["website"] in kept_websites
    ]
    save_state(PIPELINE_PATH, state)
    print(f"\n  → {len(kept)} leads passed audit (score ≥ 3) | {discarded_count} discarded")

    if not kept:
        print("  No leads passed the brand audit. Try a different market or lower min_score.")
        return

    # ── Stage 3: ENRICH ──────────────────────────────────────────
    print("\n=== STAGE 3: ENRICH (finding stakeholder emails) ===")
    from src.scraper.email_scraper import enrich_companies
    enriched = enrich_companies()
    print(f"\n  → Total enriched: {len(enriched)}")

    # ── Stage 4: GENERATE ────────────────────────────────────────
    print("\n=== STAGE 4: GENERATE (personalizing emails) ===")
    from src.generator.email_generator import generate_emails
    generated = generate_emails()
    print(f"\n  → {len(generated)} emails ready")

    # ── Stage 5: NOTION CRM ──────────────────────────────────────
    print("\n=== STAGE 5: NOTION CRM (pushing leads) ===")
    from src.crm.notion_client import push_leads_to_notion
    notion_pushed = push_leads_to_notion(generated)

    # ── Stage 6: GMAIL DRAFTS ────────────────────────────────────
    print("\n=== STAGE 6: GMAIL DRAFTS ===")
    try:
        from src.sender.gmail_sender import create_drafts_from_generated
        create_drafts_from_generated()
        drafts_created = True
    except Exception as e:
        print(f"  [DRAFTS] Skipped — {e}")
        drafts_created = False

    # ── SUMMARY ──────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("  SUMMARY")
    print("=" * 60)
    state = load_state(PIPELINE_PATH)

    total_discovered = len(discovered)
    total_audited = len(kept)
    total_enriched = sum(1 for c in enriched if c.get("stakeholder_email"))
    total_generated = len(generated)

    print(f"  Discovered:      {total_discovered} companies in {kept[0].get('geo_label', '?') if kept else '?'}")
    print(f"  Passed audit:    {total_audited} (score ≥ 3)")
    print(f"  Discarded:       {discarded_count}")
    print(f"  Emails found:    {total_enriched}")
    print(f"  Emails generated:{total_generated}")
    print(f"  Pushed to Notion:{notion_pushed}")
    print(f"  Gmail drafts:    {'created' if drafts_created else 'skipped (no credentials)'}")

    if generated:
        # Most promising = highest checklist score
        best = max(generated, key=lambda c: c.get("checklist_score") or 0)
        print(f"\n  Most promising lead: {best.get('company_name')} ({best.get('website')})")
        print(f"    Score: {best.get('checklist_score')}/5 | {best.get('country', '')} | {best.get('industry', '')}")
        print(f"    Contact: {best.get('stakeholder_name', '?')} — {best.get('stakeholder_role', '?')}")
        print(f"    Email: {best.get('stakeholder_email', 'NOT FOUND')}")

    print("\n  Next step: review Gmail Drafts, then send with:")
    print("  python update_drafts.py --send-drafts")
    print("=" * 60)
