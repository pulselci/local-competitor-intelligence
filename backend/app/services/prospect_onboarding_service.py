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

import logging
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
) -> OnboardingResult:
    """
    Full onboarding pipeline for a new free-report prospect.
    Safe to call in a background thread — all exceptions are caught and logged.
    """
    try:
        logger.info(
            "Starting prospect onboarding: business=%r city=%r state=%r competitors=%r",
            business_name, city, state, competitor_names,
        )

        # ------------------------------------------------------------------
        # 1. Resolve Place IDs
        # ------------------------------------------------------------------
        business_place = resolve_place_id(business_name, city, state)
        if not business_place:
            logger.warning(
                "Could not resolve Place ID for business %r — continuing without it",
                business_name,
            )

        competitors_in: list[CompetitorIn] = []

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
        for comp_name in competitor_names:
            comp_name = comp_name.strip()
            if not comp_name:
                continue
            comp_place = resolve_place_id(comp_name, city, state)
            if not comp_place:
                logger.warning(
                    "Could not resolve Place ID for competitor %r — adding without it",
                    comp_name,
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
        # 2. Create business + competitor records
        # ------------------------------------------------------------------
        notes = (
            f"Free report prospect. Contact: {contact_name} "
            f"<{contact_email}> {contact_phone}".strip()
        )

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
        # 3. Collect initial snapshots (current rating + review count)
        # ------------------------------------------------------------------
        try:
            collect_snapshots_for_business(business_id)
            logger.info("Snapshots collected for %s", business_id)
        except Exception as exc:
            logger.warning("Snapshot collection failed for %s: %s", business_id, exc)

        # ------------------------------------------------------------------
        # 4. Ingest reviews (customer perception text)
        # ------------------------------------------------------------------
        try:
            ingest_reviews_for_business(str(business_id))
            logger.info("Reviews ingested for %s", business_id)
        except Exception as exc:
            logger.warning("Review ingestion failed for %s: %s", business_id, exc)

        # ------------------------------------------------------------------
        # 5. Ensure a schedule record exists (required by generated_reports FK)
        # ------------------------------------------------------------------
        try:
            upsert_schedule_for_business(
                business_id,
                frequency="monthly",
                day_of_week=None,
                day_of_month=1,
                hour=8,
                minute=0,
                timezone="America/New_York",
                is_enabled=False,   # disabled until they become a paying client
                next_run_at=None,
            )
            logger.info("Schedule upserted for %s", business_id)
        except Exception as exc:
            logger.warning("Schedule upsert failed for %s: %s", business_id, exc)

        # ------------------------------------------------------------------
        # 6. Generate first report
        # ------------------------------------------------------------------
        report_id: Optional[str] = None
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

        # ------------------------------------------------------------------
        # 7. Mark report as free preview (blurs premium sections in PDF)
        # ------------------------------------------------------------------
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

        # ---------------------