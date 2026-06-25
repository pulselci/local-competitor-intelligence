"""
Follow-up email sequences — runs daily via /cron/send-followups.

Two sequences:

1. POST-COLD-EMAIL (outreach_prospects table)
   Day-5  — gentle nudge if no reply
   Day-12 — market insight angle

2. POST-FREE-REPORT (report_delivery_logs + businesses)
   Day-5  — "did you get a chance to look?"
   Day-12 — specific market data point + soft pitch
   Day-21 — direct subscription pitch

Sends via OUTREACH_SMTP (craig@pulselci.com) so replies go to Craig,
not the automated reports@ inbox.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from uuid import UUID

from app.core.db import get_conn
from app.services.email_service import send_plain_email

logger = logging.getLogger(__name__)

PRICING_URL = "https://pulselci.com/#pricing"
FREE_REPORT_URL = "https://pulselci.com/#free-report"
BACKEND_URL = "https://pulse-lci-api.onrender.com"

_STATIC = Path(__file__).resolve().parent.parent / "static"
_BUSINESS_ONESHEET = _STATIC / "pulse_lci_business_onesheet.pdf"


# ── helpers ──────────────────────────────────────────────────────────────────

def _parse_contact_name(notes: str) -> str:
    """Extract contact name from business notes field."""
    m = re.search(r"Contact:\s*([^<\n]+?)(?:\s*<|$)", notes or "")
    return m.group(1).strip().split()[0] if m else "there"


def _unsub_footer(record_id: str, record_type: str) -> str:
    """CAN-SPAM required unsubscribe footer."""
    url = f"{BACKEND_URL}/unsubscribe?id={record_id}&type={record_type}"
    return (
        "\n\n---\n"
        f"To stop receiving emails from Pulse LCI: {url}\n"
        "Pulse LCI · United States"
    )


def _send(
    to_email: str,
    subject: str,
    body: str,
    attach_onesheet: bool = False,
    in_reply_to: str | None = None,
) -> bool:
    attachment_path = str(_BUSINESS_ONESHEET) if attach_onesheet and _BUSINESS_ONESHEET.exists() else None
    result = send_plain_email(
        to_email=to_email,
        subject=subject,
        body=body,
        attachment_path=attachment_path,
        attachment_filename="Pulse_LCI_Overview.pdf" if attachment_path else None,
        in_reply_to=in_reply_to,
    )
    if not result.ok:
        logger.warning("Follow-up send failed to %s: %s", to_email, result.error)
    return result.ok


# ── cold email follow-ups ─────────────────────────────────────────────────────

def run_cold_email_followups() -> dict:
    """
    Day-5 and Day-12 follow-ups for outreach_prospects with status='sent'.

    Group A (drive-to-website): separate emails, PDF on day 5, link-focused copy.
    Group B (reply-ask): threaded as a reply to the original email, no PDF, no links,
                         conversational copy that continues the reply-ask tone.

    Uses a +-1 day window to avoid missing sends due to cron timing.
    """
    sent1 = sent2 = 0

    with get_conn() as conn:
        with conn.cursor() as cur:

            # ── Day-5 follow-ups ──────────────────────────────────────────────
            cur.execute("""
                SELECT id, business_name, contact_email, city, state,
                       draft_subject, top_competitor_name, ab_group, message_id
                FROM outreach_prospects
                WHERE status = 'sent'
                  AND email_unsubscribed = FALSE
                  AND followup1_sent_at IS NULL
                  AND sent_at IS NOT NULL
                  AND sent_at >= NOW() - INTERVAL '7 days'
                  AND sent_at <= NOW() - INTERVAL '4 days'
                  AND contact_email IS NOT NULL
            """)
            day5_prospects = cur.fetchall()

            for p in day5_prospects:
                market = f"{p['city']}, {p['state']}" if p.get('city') else "your market"
                ab = p.get('ab_group') or 'A'
                orig_subject = p.get('draft_subject') or f"competitive snapshot for {p['business_name']}"

                if ab == 'B':
                    # Group B — threaded reply, no PDF, no links, reply-ask tone
                    body = (
                        f"Hi,\n\n"
                        f"Just circling back on my last note. Still happy to send that free competitive report for "
                        f"{p['business_name']} — no link to click, just reply here and I'll send it straight to your inbox.\n\n"
                        f"Craig\n"
                        f"Pulse LCI"
                        + _unsub_footer(str(p['id']), "prospect")
                    )
                    subject = f"Re: {orig_subject}"
                    ok = _send(
                        p['contact_email'], subject, body,
                        attach_onesheet=False,
                        in_reply_to=p.get('message_id'),
                    )
                else:
                    # Group A — separate email, PDF attached, link-focused
                    body = (
                        f"Hi,\n\n"
                        f"Just making sure this didn't get buried. Happy to run the free "
                        f"competitive snapshot for {p['business_name']} whenever it works for you.\n\n"
                        f"It shows exactly where you stand against local competitors in {market} "
                        f"and what to focus on this month. Free report lands in your inbox in less than 5 minutes.\n\n"
                        f"{FREE_REPORT_URL}\n\n"
                        f"Craig\n"
                        f"Pulse LCI"
                        + _unsub_footer(str(p['id']), "prospect")
                    )
                    subject = f"Re: {orig_subject}"
                    ok = _send(p['contact_email'], subject, body, attach_onesheet=True)

                if ok:
                    cur.execute(
                        "UPDATE outreach_prospects SET followup1_sent_at = NOW() WHERE id = %s",
                        (p['id'],)
                    )
                    sent1 += 1

            # ── Day-12 follow-ups ─────────────────────────────────────────────
            cur.execute("""
                SELECT id, business_name, contact_email, city, state,
                       top_competitor_name, reviews_count, rating, ab_group, message_id
                FROM outreach_prospects
                WHERE status = 'sent'
                  AND email_unsubscribed = FALSE
                  AND followup2_sent_at IS NULL
                  AND sent_at IS NOT NULL
                  AND sent_at >= NOW() - INTERVAL '14 days'
                  AND sent_at <= NOW() - INTERVAL '11 days'
                  AND contact_email IS NOT NULL
            """)
            day12_prospects = cur.fetchall()

            for p in day12_prospects:
                market = f"{p['city']}, {p['state']}" if p.get('city') else "your market"
                ab = p.get('ab_group') or 'A'
                competitor = p.get('top_competitor_name')

                if ab == 'B':
                    # Group B — threaded final nudge, purely conversational
                    comp_line = (
                        f"For what it's worth, I've been watching {competitor} pick up reviews in {market} over the past few weeks."
                        if competitor
                        else f"I've been watching the competitive landscape in {market} shift over the past few weeks."
                    )
                    body = (
                        f"Hi,\n\n"
                        f"Last note from me — I don't want to keep filling your inbox.\n\n"
                        f"{comp_line}\n\n"
                        f"If you're ever curious how {p['business_name']} stacks up, just reply and I'll send the report over. "
                        f"No strings, no link to click.\n\n"
                        f"Craig\n"
                        f"Pulse LCI"
                        + _unsub_footer(str(p['id']), "prospect")
                    )
                    subject = f"Re: last note"
                    ok = _send(
                        p['contact_email'], subject, body,
                        attach_onesheet=False,
                        in_reply_to=p.get('message_id'),
                    )
                else:
                    # Group A — separate email, market insight angle
                    competitor_line = (
                        f"{competitor} has been building review momentum recently."
                        if competitor
                        else f"competitors in {market} have been gaining review ground recently."
                    )
                    body = (
                        f"Hi,\n\n"
                        f"One thing I noticed while tracking {market}: {competitor_line}\n\n"
                        f"If you'd like to see where {p['business_name']} stands in comparison, "
                        f"I can pull a free competitive snapshot this week. No strings attached.\n\n"
                        f"{FREE_REPORT_URL}\n\n"
                        f"Craig\n"
                        f"Pulse LCI"
                        + _unsub_footer(str(p['id']), "prospect")
                    )
                    subject = f"One thing I noticed in {market}'s market"
                    ok = _send(p['contact_email'], subject, body)

                if ok:
                    cur.execute(
                        "UPDATE outreach_prospects SET followup2_sent_at = NOW() WHERE id = %s",
                        (p['id'],)
                    )
                    sent2 += 1

        conn.commit()

    logger.info("Cold email follow-ups: Day-5=%d Day-12=%d", sent1, sent2)
    return {"cold_day5": sent1, "cold_day12": sent2}


# ── post-free-report follow-ups ───────────────────────────────────────────────

def run_report_followups() -> dict:
    """
    Day-5, Day-12, and Day-21 follow-ups for businesses that received a
    free report but have not subscribed yet.
    Skips businesses who have unsubscribed.
    Tracks sends in prospect_followup_log to prevent duplicates.
    """
    counts = {5: 0, 12: 0, 21: 0}

    windows = [
        (5,  "4 days",  "7 days"),
        (12, "11 days", "14 days"),
        (21, "20 days", "24 days"),
    ]

    with get_conn() as conn:
        with conn.cursor() as cur:
            for day, min_age, max_age in windows:
                cur.execute(f"""
                    SELECT DISTINCT
                        rdl.report_id,
                        b.id   AS business_id,
                        b.name AS business_name,
                        b.notes,
                        rdl.recipient_email,
                        rdl.sent_at
                    FROM report_delivery_logs rdl
                    JOIN generated_reports gr ON gr.id = rdl.report_id
                    JOIN businesses b ON b.id = gr.business_id
                    WHERE rdl.status = 'sent'
                      AND rdl.sent_at >= NOW() - INTERVAL '{max_age}'
                      AND rdl.sent_at <= NOW() - INTERVAL '{min_age}'
                      AND rdl.recipient_email IS NOT NULL
                      AND b.email_unsubscribed = FALSE
                      -- not yet a paying subscriber
                      AND NOT EXISTS (
                          SELECT 1 FROM report_schedules rs
                          WHERE rs.business_id = b.id AND rs.is_enabled = true
                      )
                      -- follow-up not already sent for this day
                      AND NOT EXISTS (
                          SELECT 1 FROM prospect_followup_log pfl
                          WHERE pfl.business_id = b.id AND pfl.day = {day}
                      )
                """)
                rows = cur.fetchall()

                for row in rows:
                    name = _parse_contact_name(row['notes'] or "")
                    business = row['business_name']
                    email = row['recipient_email']
                    business_id = str(row['business_id'])

                    ok = False
                    if day == 5:
                        ok = _send(email, *_report_followup_day5(name, business, business_id), attach_onesheet=True)
                    elif day == 12:
                        ok = _send(email, *_report_followup_day12(name, business, business_id))
                    elif day == 21:
                        ok = _send(email, *_report_followup_day21(name, business, business_id))

                    if ok:
                        cur.execute(
                            """
                            INSERT INTO prospect_followup_log (business_id, day, to_email)
                            VALUES (%s, %s, %s)
                            ON CONFLICT (business_id, day) DO NOTHING
                            """,
                            (business_id, day, email),
                        )
                        counts[day] += 1

        conn.commit()

    logger.info(
        "Report