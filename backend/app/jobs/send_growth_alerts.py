"""
Weekly market alert emails for Growth plan subscribers only.

Runs via POST /cron/send-growth-alerts (called from Render cron, weekly).

Checks the last 7 days of snapshot deltas per competitor and fires a
single combined alert email if any threshold is crossed. Never sends
more than one alert per subscriber per week, and never within 5 days
of their scheduled monthly report.

Thresholds:
  - Competitor gained 10+ reviews in the last 7 days
  - Competitor rating moved +/- 0.2 or more in the last 7 days
  - Competitor dropped below 4.0 stars (crossed the threshold this week)
  - New competitor appeared in their market (added in last 8 days)
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone, timedelta

from app.core.config import settings
from app.core.db import get_conn
from app.services.analytics_service import compute_snapshot_deltas
from app.services.email_service import send_plain_email

logger = logging.getLogger(__name__)

REVIEW_SPIKE_THRESHOLD  = 10     # reviews gained in 7 days
RATING_MOVE_THRESHOLD   = 0.2    # points up or down in 7 days
RATING_DANGER_THRESHOLD = 4.0    # competitor crossing below this is notable
NO_ALERT_BEFORE_REPORT_DAYS = 5  # days before monthly report — stay silent


def _parse_contact_email(notes: str) -> str | None:
    m = re.search(r"<([^>]+@[^>]+)>", notes or "")
    return m.group(1).strip() if m else None


def _parse_contact_name(notes: str) -> str:
    m = re.search(r"Contact:\s*([^<\n]+?)(?:\s*<|$)", notes or "")
    return m.group(1).strip().split()[0] if m else "there"


def _already_alerted_this_week(conn, business_id: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1 FROM growth_alert_log
            WHERE business_id = %s
              AND sent_at >= NOW() - INTERVAL '7 days'
            LIMIT 1
            """,
            (business_id,),
        )
        return cur.fetchone() is not None


def _too_close_to_monthly_report(next_run_at) -> bool:
    if not next_run_at:
        return False
    now = datetime.now(timezone.utc)
    if next_run_at.tzinfo is None:
        next_run_at = next_run_at.replace(tzinfo=timezone.utc)
    days_until_report = (next_run_at - now).days
    return 0 <= days_until_report <= NO_ALERT_BEFORE_REPORT_DAYS


def _check_new_competitors(conn, business_id: str) -> list[str]:
    """Returns names of competitors added in the last 8 days."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT name FROM competitors
            WHERE business_id = %s
              AND created_at >= NOW() - INTERVAL '8 days'
            """,
            (business_id,),
        )
        return [r["name"] for r in cur.fetchall()]


def _build_alert_lines(deltas: list[dict], new_competitors: list[str]) -> list[dict]:
    """
    Returns a list of alert dicts: {type, competitor, detail}
    Only includes items that crossed a threshold.
    """
    alerts = []

    for d in deltas:
        name    = d.get("competitor_name") or "A competitor"
        reviews = d.get("google_review_count") or 0
        delta7  = d.get("reviews_delta_7d")
        rdelta7 = d.get("rating_delta_7d")
        rating  = d.get("google_rating")

        # Review velocity spike
        if delta7 is not None and delta7 >= REVIEW_SPIKE_THRESHOLD:
            alerts.append({
                "type":       "review_spike",
                "competitor": name,
                "detail":     f"{name} picked up {int(delta7)} new reviews this week "
                              f"(now at {reviews}).",
            })

        # Significant rating move
        if rdelta7 is not None and abs(float(rdelta7)) >= RATING_MOVE_THRESHOLD:
            direction = "up" if float(rdelta7) > 0 else "down"
            alerts.append({
                "type":       "rating_move",
                "competitor": name,
                "detail":     f"{name}'s rating moved {direction} {abs(float(rdelta7)):.1f} "
                              f"points this week (now {rating}).",
            })

        # Competitor crossed below 4.0 this week
        if (
            rating is not None
            and rdelta7 is not None
            and float(rating) < RATING_DANGER_THRESHOLD
            and float(rating) - float(rdelta7) >= RATING_DANGER_THRESHOLD
        ):
            alerts.append({
                "type":       "rating_danger",
                "competitor": name,
                "detail":     f"{name} dropped below 4.0 stars this week ({rating}). "
                              f"Customers will notice.",
            })

    for comp_name in new_competitors:
        alerts.append({
            "type":       "new_competitor",
            "competitor": comp_name,
            "detail":     f"A new competitor appeared in your market: {comp_name}.",
        })

    return alerts


def _build_email(name: str, business: str, alert_lines: list[dict]) -> tuple[str, str]:
    subject = f"Market update for {business} this week"

    intro = f"Hi {name},\n\nA few things moved in your market this week worth knowing about.\n\n"

    body_lines = "\n".join(f"- {a['detail']}" for a in alert_lines)

    outro = (
        "\n\nThese signals can shift quickly. Your next monthly report will include "
        "the full picture, but wanted to flag these now.\n\n"
        "Reply to this email if you have questions.\n\n"
        "Craig\n"
        "Pulse LCI"
    )

    return subject, intro + body_lines + outro


def run_growth_alerts() -> dict:
    """
    Main entry point. Checks all Growth subscribers and sends alert
    emails where thresholds are crossed. Returns a summary dict.
    """
    sent = 0
    skipped_no_delta = 0
    skipped_already_sent = 0
    skipped_report_soon = 0

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    b.id::text   AS business_id,
                    b.name       AS business_name,
                    b.notes,
                    rs.next_run_at
                FROM businesses b
                JOIN report_schedules rs ON rs.business_id = b.id
                WHERE b.is_active = true
                  AND b.stripe_price_id = %s
                  AND rs.is_enabled = true
                """,
                (settings.stripe_price_growth or "__none__",),
            )
            growth_subscribers = cur.fetchall()

        logger.info("Growth alert check: %d subscribers", len(growth_subscribers))

        for sub in growth_subscribers:
            business_id   = sub["business_id"]
            business_name = sub["business_name"]
            notes         = sub.get("notes") or ""
            next_run_at   = sub.get("next_run_at")

            contact_email = _parse_contact_email(notes)
            contact_name  = _parse_contact_name(notes)

            if not contact_email:
                logger.warning("No contact email for %s — skipping", business_name)
                continue

            # Guard: already sent this week
            if _already_alerted_this_week(conn, business_id):
                skipped_already_sent += 1
                continue

            # Guard: too close to monthly report
            if _too_close_to_monthly_report(next_run_at):
                skipped_report_soon += 1
                continue

            # Compute 7-day deltas
            try:
                deltas = compute_snapshot_deltas(business_id, days=8)
            except Exception as exc:
                logger.warning("Delta compute failed for %s: %s", business_name, exc)
                continue

            # Check for new competitors
            new_comps = _check_new_competitors(conn, business_id)

            # Build alert list
            alert_lines = _build_alert_lines(deltas, new_comps)

            if not alert_lines:
                skipped_no_delta += 1
                continue

            # Send email
            subject, body = _build_email(contact_name, business_name, alert_lines)
            result = send_plain_email(
                to_email=contact_email,
                subject=subject,
                body=body,
            )

            if result.ok:
                # Log to prevent duplicate this week
                triggers = [a["type"] for a in alert_lines]
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO growth_alert_log
                            (business_id, to_email, triggers)
                        VALUES (%s, %s, %s)
                        """,
                        (business_id, contact_email, json.dumps(triggers)),
                    )
                conn.commit()
                sent += 1
                logger.info(
                    "Alert sent to %s (%s): %s",
                    business_name, contact_email, triggers,
                )
            else:
                logger.warning(
                    "Alert email failed for %s: %s", business_name, result.error
                )

    logger.info(
        "Growth alerts complete: sent=%d skipped_no_change=%d "
        "skipped_already_sent=%d skipped_report_soon=%d",
        sent, skipped_no_delta, skipped_already_sent, skipped_report_soon,
    )
    return {
        "sent":                  sent,
        "skipped_no_change":     skipped_no_delta,
        "skipped_already_sent":  skipped_already_sent,
        "skipped_report_soon":   skipped_report_soon,
    }
