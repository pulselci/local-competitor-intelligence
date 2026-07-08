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
    All sequences are reply-first: threaded replies, no PDF, single CTA.
    """
    sent1 = sent2 = 0

    with get_conn() as conn:
        with conn.cursor() as cur:

            # Day-5 follow-ups
            cur.execute("""
                SELECT id, business_name, contact_email, city, state,
                       draft_subject, top_competitor_name, message_id
                FROM outreach_prospects
                WHERE status = 'sent'
                  AND COALESCE(prospect_type, 'local_business') != 'agency'
                  AND email_unsubscribed = FALSE
                  AND followup1_sent_at IS NULL
                  AND sent_at IS NOT NULL
                  AND sent_at >= NOW() - INTERVAL '7 days'
                  AND sent_at <= NOW() - INTERVAL '4 days'
                  AND contact_email IS NOT NULL
            """)
            for p in cur.fetchall():
                orig_subject = p.get('draft_subject') or f"competitive snapshot for {p['business_name']}"
                body = (
                    f"Hi,\n\n"
                    f"Just circling back on my last note. Still happy to pull that free competitive report for {p['business_name']}.\n\n"
                    f"Reply with your top 2-3 competitors and I'll send it over.\n\n"
                    f"Craig\n"
                    f"pulselci.com"
                    + _unsub_footer(str(p['id']), "prospect")
                )
                ok = _send(p['contact_email'], f"Re: {orig_subject}", body,
                           attach_onesheet=False, in_reply_to=p.get('message_id'))
                if ok:
                    cur.execute("UPDATE outreach_prospects SET followup1_sent_at = NOW() WHERE id = %s", (p['id'],))
                    sent1 += 1

            # Day-12 follow-ups
            cur.execute("""
                SELECT id, business_name, contact_email, city, state,
                       draft_subject, top_competitor_name, reviews_count, rating, message_id
                FROM outreach_prospects
                WHERE status = 'sent'
                  AND COALESCE(prospect_type, 'local_business') != 'agency'
                  AND email_unsubscribed = FALSE
                  AND followup2_sent_at IS NULL
                  AND sent_at IS NOT NULL
                  AND sent_at >= NOW() - INTERVAL '14 days'
                  AND sent_at <= NOW() - INTERVAL '11 days'
                  AND contact_email IS NOT NULL
            """)
            for p in cur.fetchall():
                market = f"{p['city']}, {p['state']}" if p.get('city') else "your market"
                competitor = p.get('top_competitor_name')
                comp_line = (
                    f"For what it's worth, I've been watching {competitor} pick up reviews in {market} over the past few weeks."
                    if competitor
                    else f"I've been watching the competitive landscape in {market} shift over the past few weeks."
                )
                body = (
                    f"Hi,\n\n"
                    f"Last note from me.\n\n"
                    f"{comp_line}\n\n"
                    f"If you're ever curious how {p['business_name']} stacks up, just reply and I'll send the report over.\n\n"
                    f"Craig\n"
                    f"pulselci.com"
                    + _unsub_footer(str(p['id']), "prospect")
                )
                ok = _send(p['contact_email'], f"Re: last note", body,
                           attach_onesheet=False, in_reply_to=p.get('message_id'))
                if ok:
                    cur.execute("UPDATE outreach_prospects SET followup2_sent_at = NOW() WHERE id = %s", (p['id'],))
                    sent2 += 1


            # ── Agency Day-5 follow-ups ─────────────────────────────────────
            cur.execute("""
                SELECT id, business_name, contact_email, city, state,
                       draft_subject, message_id
                FROM outreach_prospects
                WHERE status = 'sent'
                  AND prospect_type = 'agency'
                  AND email_unsubscribed = FALSE
                  AND followup1_sent_at IS NULL
                  AND sent_at IS NOT NULL
                  AND sent_at >= NOW() - INTERVAL '7 days'
                  AND sent_at <= NOW() - INTERVAL '4 days'
                  AND contact_email IS NOT NULL
            """)
            for p in cur.fetchall():
                orig_subject = p.get('draft_subject') or 'partner opportunity'
                city = p.get('city') or 'your market'
                body = (
                    f"Hi,\n\n"
                    f"Just following up on my last note.\n\n"
                    f"I can pull a sample competitive report for a business in {city} in about a minute. Shows review momentum, rating gaps, and how they stack up against local competitors. A few agencies have started including this kind of report as part of their monthly client deliverables.\n\n"
                    f"Happy to send one over if it would be useful. Just reply and I'll get it to you same day.\n\n"
                    f"Craig\n"
                    f"pulselci.com"
                    + _unsub_footer(str(p['id']), "prospect")
                )
                ok = _send(p['contact_email'], f"Re: {orig_subject}", body,
                           attach_onesheet=False, in_reply_to=p.get('message_id'))
                if ok:
                    cur.execute("UPDATE outreach_prospects SET followup1_sent_at = NOW() WHERE id = %s", (p['id'],))
                    sent1 += 1

            # ── Agency Day-12 follow-ups ─────────────────────────────────────
            cur.execute("""
                SELECT id, business_name, contact_email, city, state,
                       draft_subject, message_id
                FROM outreach_prospects
                WHERE status = 'sent'
                  AND prospect_type = 'agency'
                  AND email_unsubscribed = FALSE
                  AND followup2_sent_at IS NULL
                  AND sent_at IS NOT NULL
                  AND sent_at >= NOW() - INTERVAL '14 days'
                  AND sent_at <= NOW() - INTERVAL '11 days'
                  AND contact_email IS NOT NULL
            """)
            for p in cur.fetchall():
                orig_subject = p.get('draft_subject') or 'partner opportunity'
                body = (
                    f"Hi,\n\n"
                    f"Last note from me on this.\n\n"
                    f"If adding a competitive intelligence layer to your client reporting ever makes sense, I'm happy to pull a sample anytime. No call needed, just reply.\n\n"
                    f"Craig\n"
                    f"pulselci.com"
                    + _unsub_footer(str(p['id']), "prospect")
                )
                ok = _send(p['contact_email'], f"Re: {orig_subject}", body,
                           attach_onesheet=False, in_reply_to=p.get('message_id'))
                if ok:
                    cur.execute("UPDATE outreach_prospects SET followup2_sent_at = NOW() WHERE id = %s", (p['id'],))
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
        "Report follow-ups: Day-5=%d Day-12=%d Day-21=%d",
        counts[5], counts[12], counts[21],
    )
    return {"report_day5": counts[5], "report_day12": counts[12], "report_day21": counts[21]}


# ── email templates ───────────────────────────────────────────────────────────

def _report_followup_day5(name: str, business: str, business_id: str) -> tuple[str, str]:
    greeting = name if name != "there" else ""
    subject = f"did you get a chance to look?" if not greeting else f"did you get a chance to look, {greeting}?"
    body = (
        f"Hi{' ' + greeting if greeting else ''},\n\n"
        f"Just checking in on the competitive report for {business}.\n\n"
        f"The friction signals section is worth a look if you haven't gotten there yet. "
        f"It shows which complaint themes are showing up across your market and how your "
        f"competitors compare.\n\n"
        f"Happy to answer any questions, just reply here.\n\n"
        f"Craig\n"
        f"Pulse LCI"
        + _unsub_footer(business_id, "business")
    )
    return subject, body


def _report_followup_day12(name: str, business: str, business_id: str) -> tuple[str, str]:
    greeting = name if name != "there" else ""
    subject = f"one thing worth knowing about your market"
    body = (
        f"Hi{' ' + greeting if greeting else ''},\n\n"
        f"Wanted to follow up on the report for {business}.\n\n"
        f"Your market moves every month. Review counts shift, complaint patterns change, "
        f"and competitors gain or lose ground. The snapshot you received shows where things "
        f"stood when we ran it, but that picture is already getting older.\n\n"
        f"For $99/month you'd get this updated every month, tracking exactly how your "
        f"competitive position is shifting and what to focus on. Cancel anytime, no contracts.\n\n"
        f"Worth trying for one month? {PRICING_URL}\n\n"
        f"Craig\n"
        f"Pulse LCI"
        + _unsub_footer(business_id, "business")
    )
    return subject, body


def _report_followup_day21(name: str, business: str, business_id: str) -> tuple[str, str]:
    greeting = name if name != "there" else ""
    subject = f"last note on this"
    body = (
        f"Hi{' ' + greeting if greeting else ''},\n\n"
        f"Last follow-up on this.\n\n"
        f"The report we sent gives you a baseline. What it can't show you is what's "
        f"changing: which competitor is quietly gaining ground, which complaint themes "
        f"are rising in your market, and whether the gap is widening or closing.\n\n"
        f"That's what the monthly subscription covers. $99/month, report in your inbox "
        f"every month, cancel anytime. {PRICING_URL}\n\n"
        f"If the timing isn't right, no worries. The link is there whenever it makes sense.\n\n"
        f"Craig\n"
        f"Pulse LCI"
        + _unsub_footer(business_id, "business")
    )
    return subject, body


# ── targeted outreach follow-ups ─────────────────────────────────────────────

def run_targeted_followups() -> dict:
    """
    Day-5 and Day-12 follow-ups for targeted_prospects with status='sent'.
    Different copy from cold email -- they already have the report.
    """
    sent1 = sent2 = 0

    with get_conn() as conn:
        with conn.cursor() as cur:

            # Day-5
            cur.execute("""
                SELECT id, business_name, contact_email, sent_at, competitor_names
                FROM targeted_prospects
                WHERE status = 'sent'
                  AND followup1_sent_at IS NULL
                  AND sent_at IS NOT NULL
                  AND sent_at >= NOW() - INTERVAL '7 days'
                  AND sent_at <= NOW() - INTERVAL '4 days'
                  AND contact_email IS NOT NULL
            """)
            for p in cur.fetchall():
                comps = list(p.get("competitor_names") or [])
                if len(comps) == 1:
                    comp_str = comps[0]
                elif len(comps) == 2:
                    comp_str = f"{comps[0]} and {comps[1]}"
                elif len(comps) >= 3:
                    comp_str = f"{comps[0]}, {comps[1]}, and {comps[2]}"
                else:
                    comp_str = "your competitors"
                body = (
                    f"Hi,\n\n"
                    f"Just checking in -- did you get a chance to look at the report for {p['business_name']}?\n\n"
                    f"Curious what you thought of how you stack up against {comp_str}. "
                    f"Happy to answer any questions or pull an updated version with different competitors if useful.\n\n"
                    f"Craig\n"
                    f"pulselci.com"
                    + _unsub_footer(str(p['id']), "targeted")
                )
                ok = _send(p['contact_email'], f"Re: competitive snapshot for {p['business_name']}", body)
                if ok:
                    cur.execute(
                        "UPDATE targeted_prospects SET followup1_sent_at = NOW() WHERE id = %s", (p['id'],)
                    )
                    sent1 += 1

            # Day-12
            cur.execute("""
                SELECT id, business_name, contact_email, sent_at
                FROM targeted_prospects
                WHERE status = 'sent'
                  AND followup2_sent_at IS NULL
                  AND sent_at IS NOT NULL
                  AND sent_at >= NOW() - INTERVAL '14 days'
                  AND sent_at <= NOW() - INTERVAL '11 days'
                  AND contact_email IS NOT NULL
            """)
            for p in cur.fetchall():
                body = (
                    f"Hi,\n\n"
                    f"Last note from me on this.\n\n"
                    f"If the report was useful and you want your competitive data updated monthly, "
                    f"that's exactly what Pulse LCI does -- $99/month, cancel anytime.\n\n"
                    f"{PRICING_URL}\n\n"
                    f"Craig\n"
                    f"pulselci.com"
                    + _unsub_footer(str(p['id']), "targeted")
                )
                ok = _send(p['contact_email'], f"Re: competitive snapshot for {p['business_name']}", body)
                if ok:
                    cur.execute(
                        "UPDATE targeted_prospects SET followup2_sent_at = NOW() WHERE id = %s", (p['id'],)
                    )
                    sent2 += 1

        conn.commit()

    logger.info("Targeted follow-ups: Day-5=%d Day-12=%d", sent1, sent2)
    return {"targeted_day5": sent1, "targeted_day12": sent2}


# ── main entry ────────────────────────────────────────────────────────────────

def run_all_followups() -> dict:
    cold = run_cold_email_followups()
    report = run_report_followups()
    targeted = run_targeted_followups()
    return {**cold, **report, **targeted}
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         