"""
Prospect onboarding pipeline.

Given a form submission (name, email, business name, city, state,
and up to 3 competitor names), this service:

  1. Resolves Google Place IDs for the business + each competitor
  2. Creates the business + competitor records in the DB
  3. Collects an initial snapshot (current rating + review count)
  4. Ingests the most recent reviews (for perception analysis)
  5. Generates a first report
  6. Emails the report as a PDF to the prospect

The first report gracefully omits metrics that require 30 days of
snapshot history (reviews_delta_30d). Everything else — share of voice,
competitive rankings, customer perception, review text themes — is
fully accurate from day one.
"""
from __future__ import annotations

import concurrent.futures as _cf
import logging
import threading
from dataclasses import dataclass
from typing import Optional
from uuid import UUID

from app.models.schemas import BusinessIntakeIn, CompetitorIn
from app.services.business_service import create_business_and_competitors
from app.services.place_resolver import resolve_place_id
from app.services.report_schedule_service import upsert_schedule_for_business
from app.services.review_batch import ingest_reviews_for_business
from app.services.snapshot_service import collect_snapshots_for_business

logger = logging.getLogger(__name__)


def _find_existing_business(business_name: str, city: str, state: str) -> Optional[UUID]:
    """
    Look for an existing business record with the same name + city + state.
    Returns the UUID if found, None otherwise.
    This prevents duplicate records when a free-report prospect subscribes.
    """
    from app.core.db import get_conn
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id FROM businesses
                    WHERE lower(trim(name)) = lower(trim(%s))
                      AND lower(trim(city)) = lower(trim(%s))
                      AND lower(trim(state)) = lower(trim(%s))
                    ORDER BY created_at ASC
                    LIMIT 1
                    """,
                    (business_name, city, state),
                )
                row = cur.fetchone()
                if row:
                    return UUID(str(row["id"]))
    except Exception as exc:
        logger.warning("_find_existing_business error: %s", exc)
    return None



def _upsert_new_competitors(business_id: UUID, competitors_in: list) -> None:
    """
    Add any competitors from the new signup that aren't already stored for this business.
    Matches on google_place_id (preferred) or lowercase name to avoid duplicates.
    """
    from app.core.db import get_conn
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                # Fetch existing competitors for this business
                cur.execute(
                    "SELECT name, google_place_id FROM competitors WHERE business_id = %s",
                    (str(business_id),),
                )
                existing = cur.fetchall()
                existing_place_ids = {
                    r["google_place_id"] for r in existing if r.get("google_place_id")
                }
                existing_names = {
                    (r["name"] or "").lower() for r in existing
                }

                added = 0
                for c in competitors_in:
                    # Skip the is_business=True entry (the client itself)
                    if getattr(c, "is_business", False):
                        continue
                    place_id = getattr(c, "google_place_id", None)
                    name = getattr(c, "name", "") or ""
                    # Already tracked?
                    if place_id and place_id in existing_place_ids:
                        continue
                    if name.lower() in existing_names:
                        continue
                    cur.execute(
                        """
                        INSERT INTO public.competitors
                            (business_id, name, website_url, google_place_id, google_maps_url, is_business)
                        VALUES (%s, %s, %s, %s, %s, false)
                        ON CONFLICT DO NOTHING
                        """,
                        (
                            str(business_id),
                            name,
                            getattr(c, "website_url", None),
                            place_id,
                            getattr(c, "google_maps_url", None),
                        ),
                    )
                    existing_names.add(name.lower())
                    if place_id:
                        existing_place_ids.add(place_id)
                    added += 1
            conn.commit()
            logger.info("Added %d new competitor(s) to existing business %s", added, business_id)
    except Exception as exc:
        logger.warning("_upsert_new_competitors failed for %s: %s", business_id, exc)


@dataclass
class OnboardingResult:
    ok: bool
    business_id: Optional[str] = None
    report_id: Optional[str] = None
    error: Optional[str] = None


def onboard_prospect(
    *,
    contact_name: str,
    contact_email: str,
    contact_phone: str = "",
    business_name: str,
    city: str,
    state: str,
    competitor_names: list[str],
    skip_report: bool = False,
    background_data_collection: bool = False,
) -> OnboardingResult:
    """
    Full onboarding pipeline for a new prospect.
    Set skip_report=True for paid subscribers — the webhook handles report generation
    after payment confirms so we don't send a blurred free-preview report by mistake.
    Safe to call in a background thread — all exceptions are caught and logged.
    """
    try:
        logger.info(
            "Starting prospect onboarding: business=%r city=%r state=%r competitors=%r",
            business_name, city, state, competitor_names,
        )

        # ------------------------------------------------------------------
        # 1. Resolve Place IDs — all in parallel to eliminate sequential wait
        # ------------------------------------------------------------------
        clean_competitor_names = [c.strip() for c in competitor_names if c.strip()]
        all_lookup_names = [business_name] + clean_competitor_names

        logger.info("Resolving %d place IDs in parallel", len(all_lookup_names))
        with _cf.ThreadPoolExecutor(max_workers=min(len(all_lookup_names), 10)) as pool:
            place_futures = [
                pool.submit(resolve_place_id, name, city, state)
                for name in all_lookup_names
            ]
            place_results = [f.result() for f in place_futures]

        business_place = place_results[0]
        comp_place_results = place_results[1:]

        if not business_place:
            logger.warning(
                "Could not resolve Place ID for business %r — continuing without it",
                business_name,
            )

        competitors_in: list[CompetitorIn] = []

        # Track resolved names/addresses for email verification
        resolved_business_label = (
            f"{business_place.name} — {business_place.formatted_address}"
            if business_place else business_name
        )
        resolved_competitor_labels: list[str] = []

        # The client's own business as a competitor (is_business=True)
        competitors_in.append(
            CompetitorIn(
                name=business_name,
                google_place_id=business_place.place_id if business_place else None,
                google_maps_url=business_place.google_maps_url if business_place else None,
                is_business=True,
            )
        )

        # Competitors
        for comp_name, comp_place in zip(clean_competitor_names, comp_place_results):
            if not comp_place:
                logger.warning(
                    "Could not resolve Place ID for competitor %r — adding without it",
                    comp_name,
                )
            resolved_competitor_labels.append(
                f"{comp_place.name} — {comp_place.formatted_address}"
                if comp_place else f"{comp_name} (could not verify)"
            )
            competitors_in.append(
                CompetitorIn(
                    name=comp_name,
                    google_place_id=comp_place.place_id if comp_place else None,
                    google_maps_url=comp_place.google_maps_url if comp_place else None,
                    is_business=False,
                )
            )

        # ------------------------------------------------------------------
        # 2. Create business + competitor records (or reuse existing match)
        # ------------------------------------------------------------------
        notes = (
            f"Free report prospect. Contact: {contact_name} "
            f"<{contact_email}> {contact_phone}".strip()
        )

        # Check for existing business with same name + city to avoid duplicates
        existing_business_id = _find_existing_business(business_name, city, state)
        if existing_business_id:
            business_id = existing_business_id
            logger.info("Reusing existing business %s for %r", business_id, business_name)
            # Update notes to record new contact
            try:
                from app.core.db import get_conn
                with get_conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "UPDATE businesses SET notes = %s WHERE id = %s",
                            (notes, str(business_id)),
                        )
                    conn.commit()
            except Exception as exc:
                logger.warning("Could not update notes for existing business: %s", exc)

            # Add any new competitors that aren't already tracked
            _upsert_new_competitors(business_id, competitors_in)
        else:
            intake = BusinessIntakeIn(
                business_name=business_name,
                city=city,
                state=state,
                country="US",
                notes=notes,
                competitors=competitors_in,
            )
            result = create_business_and_competitors(intake)
            business_id: UUID = result.business.id
            logger.info("Created business %s for prospect %r", business_id, business_name)

        # ------------------------------------------------------------------
        # 2b. Mark matching outreach prospect as report_sent so it drops off
        #     the cold email follow-up list (they've moved to free-report flow)
        # ------------------------------------------------------------------
        try:
            from app.core.db import get_conn as _get_conn
            with _get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE outreach_prospects
                           SET status = 'report_sent', updated_at = NOW()
                         WHERE status = 'sent'
                           AND (
                               lower(trim(contact_email)) = lower(trim(%s))
                               OR lower(trim(business_name)) = lower(trim(%s))
                           )
                        """,
                        (contact_email, business_name),
                    )
                    updated = cur.rowcount
                conn.commit()
            if updated:
                logger.info("Marked %d outreach prospect(s) as report_sent for %r", updated, business_name)
        except Exception as exc:
            logger.warning("Could not update outreach_prospects status: %s", exc)

        # ------------------------------------------------------------------
        # 3–5. Collect snapshots, ingest reviews, create schedule record.
        #      These don't affect the Stripe redirect — run in background
        #      when background_data_collection=True so the caller can return
        #      the checkout URL immediately.
        # ------------------------------------------------------------------
        def _collect_data(biz_id: UUID) -> None:
            try:
                collect_snapshots_for_business(biz_id)
                logger.info("Snapshots collected for %s", biz_id)
            except Exception as exc:
                logger.warning("Snapshot collection failed for %s: %s", biz_id, exc)

            try:
                ingest_reviews_for_business(str(biz_id))
                logger.info("Reviews ingested for %s", biz_id)
            except Exception as exc:
                logger.warning("Review ingestion failed for %s: %s", biz_id, exc)

            try:
                upsert_schedule_for_business(
                    biz_id,
                    frequency="monthly",
                    day_of_week=None,
                    day_of_month=1,
                    hour=8,
                    minute=0,
                    timezone="America/New_York",
                    is_enabled=False,   # disabled until they become a paying client
                    next_run_at=None,
                )
                logger.info("Schedule upserted for %s", biz_id)
            except Exception as exc:
                logger.warning("Schedule upsert failed for %s: %s", biz_id, exc)

        if background_data_collection:
            t = threading.Thread(target=_collect_data, args=(business_id,), daemon=True)
            t.start()
            logger.info("Data collection (snapshots/reviews/schedule) backgrounded for %s", business_id)
        else:
            _collect_data(business_id)

        # ------------------------------------------------------------------
        # 6–8. Generate, mark, and email report (skipped for paid subscribers —
        #       the Stripe webhook handles this after payment confirms)
        # ------------------------------------------------------------------
        report_id: Optional[str] = None
        if not skip_report:
            try:
                from app.api.routes import generate_business_report
                report = generate_business_report(business_id)
                if hasattr(report, "model_dump"):
                    report = report.model_dump()
                elif hasattr(report, "dict"):
                    report = report.dict()
                report_id = str(report.get("id")) if isinstance(report, dict) else None
                logger.info("Report generated: %s for business %s", report_id, business_id)
            except Exception as exc:
                logger.error("Report generation failed for %s: %s", business_id, exc)
                try:
                    from app.services.email_service import send_plain_email
                    send_plain_email(
                        to_email="craigw0503@gmail.com",
                        subject=f"⚠️ Report generation failed — {business_name}",
                        body=f"Business: {business_name}\nID: {business_id}\nContact: {contact_email}\nError: {exc}",
                    )
                except Exception:
                    pass

            if report_id:
                try:
                    from app.core.db import get_conn
                    with get_conn() as conn:
                        with conn.cursor() as cur:
                            cur.execute(
                                """
                                UPDATE generated_reports
                                SET sections = sections || '{"is_free_preview": true}'::jsonb
                                WHERE id = %s
                                """,
                                (report_id,),
                            )
                        conn.commit()
                    logger.info("Marked report %s as free preview", report_id)
                except Exception as exc:
                    logger.warning("Could not mark report as free preview: %s", exc)

            if report_id and contact_email:
                try:
                    from app.api.generated_reports import send_generated_report_email, SendReportRequest
                    comp_lines = "\n".join(
                        f"  • {label}" for label in resolved_competitor_labels
                    ) or "  (none provided)"
                    verification_block = (
                        "Before you dig in, here's what we tracked down based on what you entered:\n\n"
                        f"  Your business: {resolved_business_label}\n"
                        f"  Competitors:\n{comp_lines}\n\n"
                        "If anything looks off — wrong location, wrong business, or a missing competitor — "
                        "just reply to this email and we'll fix it.\n\n"
                    )
                    send_generated_report_email(
                        UUID(report_id),
                        SendReportRequest(
                            to_email=contact_email,
                            subject=f"Your Free Competitive Intelligence Report — {business_name}",
                            body_text=(
                                f"Hi {contact_name},\n\n"
                                "Attached is your free local competitor intelligence report. "
                                "It covers your current competitive position, review standings, "
                                "and the key opportunities we spotted in your market.\n\n"
                                + verification_block +
                                "This is your baseline report. Each month you'll receive an updated "
                                "report showing exactly how your market is shifting.\n\n"
                                "Reply to this email if you have any questions.\n\n"
                                "— Pulse LCI"
                            ),
                        ),
                    )
                    logger.info("Report emailed to %s for business %s", contact_email, business_id)
                except Exception as exc:
                    logger.error("Email send failed for %s: %s", business_id, exc)
        else:
            logger.info("Skipping report generation for subscriber %s — webhook will handle it after payment", business_id)

        return OnboardingResult(
            ok=True,
            business_id=str(business_id),
            report_id=report_id,
        )

    except Exception as exc:
        logger.exception("Prospect onboarding failed for %r: %s", business_name, exc)
        return OnboardingResult(ok=False, error=str(exc))
