"""
Targeted outreach API.

Flow:
  1. POST /targeted/prospect       — create prospect, auto-suggest 3 competitors
  2. POST /targeted/{id}/confirm   — confirm/swap competitors, kick off report generation
  3. GET  /targeted/{id}/status    — poll while report generates
  4. GET  /targeted/prospects      — list queue (filterable by status)
  5. POST /targeted/{id}/approve   — render PDF, send email, mark sent
  6. POST /targeted/{id}/skip      — skip prospect
"""
from __future__ import annotations

import os
import tempfile
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

# Transparent 1×1 GIF for email open tracking
_TRACKING_PIXEL = (
    b"\x47\x49\x46\x38\x39\x61\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff"
    b"\x00\x00\x00\x21\xf9\x04\x00\x00\x00\x00\x00\x2c\x00\x00\x00\x00"
    b"\x01\x00\x01\x00\x00\x02\x02\x44\x01\x00\x3b"
)

from app.core.db import get_conn
from app.services.email_service import send_plain_email
from app.services.place_resolver import suggest_competitors

router = APIRouter(prefix="/targeted", tags=["targeted"])

# In-memory job tracker: prospect_id -> {status, error, report_id}
_gen_jobs: dict[str, dict] = {}


@router.get("/track/open/{prospect_id}", include_in_schema=False)
def track_open(prospect_id: str) -> Response:
    """Email open tracking pixel for targeted outreach emails."""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE targeted_prospects
                       SET email_open_count = COALESCE(email_open_count, 0) + 1,
                           email_opened_at  = COALESCE(email_opened_at, NOW()),
                           updated_at       = NOW()
                       WHERE id = %s""",
                    (prospect_id,),
                )
            conn.commit()
    except Exception:
        pass
    return Response(content=_TRACKING_PIXEL, media_type="image/gif")


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class CreateProspectIn(BaseModel):
    business_name: str
    contact_email: Optional[str] = None
    city: str
    state: str


class ConfirmCompetitorsIn(BaseModel):
    competitor_names: List[str]


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _get_targeted(prospect_id: str) -> dict:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM targeted_prospects WHERE id = %s", (prospect_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Targeted prospect not found")
            return dict(row)


# ---------------------------------------------------------------------------
# Background report generation
# ---------------------------------------------------------------------------

def _run_generate_bg(prospect_id: str) -> None:
    """
    Background job:
      - onboard the business (resolve place IDs, collect reviews)
      - generate full report
      - mark as full (not blurred)
      - build draft email
      - update targeted_prospect: status=ready, report_id, draft_subject, draft_body
    """
    _gen_jobs[prospect_id] = {"status": "generating", "error": None, "report_id": None}

    try:
        prospect = _get_targeted(prospect_id)
        business_name = prospect["business_name"]
        city = prospect["city"] or ""
        state = prospect["state"] or ""
        competitor_names = list(prospect["competitor_names"] or [])

        # 1. Onboard business
        from app.services.prospect_onboarding_service import onboard_prospect
        result = onboard_prospect(
            contact_name="",
            contact_email=prospect.get("contact_email") or "",
            business_name=business_name,
            city=city,
            state=state,
            competitor_names=competitor_names,
            skip_report=True,
            background_data_collection=False,
        )
        if not result.ok:
            raise RuntimeError(f"Onboarding failed: {result.error}")

        business_id = UUID(result.business_id)

        # 1b. Remove any competitors NOT in the confirmed list — prevents stale/extra
        #     competitors from previous onboardings polluting the targeted report.
        confirmed_names_lower = {n.strip().lower() for n in competitor_names if n.strip()}
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT id, name FROM competitors WHERE business_id = %s AND NOT is_business",
                        (str(business_id),),
                    )
                    all_comps = cur.fetchall()
                ids_to_delete = [
                    row[0] for row in all_comps
                    if row[1].strip().lower() not in confirmed_names_lower
                ]
                if ids_to_delete:
                    with conn.cursor() as cur2:
                        cur2.execute(
                            "DELETE FROM competitors WHERE id = ANY(%s)",
                            (ids_to_delete,),
                        )
                conn.commit()
        except Exception:
            import traceback; traceback.print_exc()

        # 2. Generate report
        from app.api.routes import generate_business_report
        report_data = generate_business_report(business_id)
        if hasattr(report_data, "model_dump"):
            report_data = report_data.model_dump()
        elif hasattr(report_data, "dict"):
            report_data = report_data.dict()
        report_id = str(report_data.get("id") or report_data.get("report_id") or "")
        if not report_id:
            raise RuntimeError("Report generation returned no ID")

        # 3. Filter report SOV to only the confirmed competitors, then mark as full
        # If the business already exists in DB with extra competitors, the report will include
        # them all — we strip those out so the targeted report only covers the confirmed set.
        confirmed_names_lower = {n.strip().lower() for n in competitor_names if n.strip()}
        import json as _json
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT sections FROM generated_reports WHERE id = %s", (report_id,))
                    _sec_row = cur.fetchone()
                if _sec_row and _sec_row[0]:
                    _sec = dict(_sec_row[0])
                    # Filter share_of_voice rows
                    _sov = _sec.get("share_of_voice") or {}
                    _sov_rows = _sov.get("rows") or []
                    _sov_rows_filtered = [
                        r for r in _sov_rows
                        if r.get("is_business")
                        or (r.get("competitor_name") or r.get("name") or "").strip().lower() in confirmed_names_lower
                    ]
                    if len(_sov_rows_filtered) < len(_sov_rows):
                        _sov["rows"] = _sov_rows_filtered
                        # Recalculate share_pct so it adds to 100
                        _total_reviews = sum(
                            int(r.get("reviews_total") or r.get("review_count") or 0)
                            for r in _sov_rows_filtered
                        )
                        if _total_reviews > 0:
                            for r in _sov_rows_filtered:
                                r_rev = int(r.get("reviews_total") or r.get("review_count") or 0)
                                r["share_pct"] = round(r_rev / _total_reviews * 100, 1)
                        _sec["share_of_voice"] = _sov
                        # Also filter share_of_voice_donut if present
                        _donut = _sec.get("share_of_voice_donut") or {}
                        if _donut.get("rows"):
                            _donut["rows"] = [
                                r for r in _donut["rows"]
                                if r.get("is_business")
                                or (r.get("competitor_name") or r.get("name") or "").strip().lower() in confirmed_names_lower
                            ]
                            _sec["share_of_voice_donut"] = _donut
                        with conn.cursor() as cur2:
                            cur2.execute(
                                "UPDATE generated_reports SET sections = %s WHERE id = %s",
                                (_json.dumps(_sec), report_id),
                            )
                        conn.commit()
        except Exception as _filter_exc:
            import traceback; traceback.print_exc()

        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE generated_reports "
                    "SET sections = sections || '{\"is_free_preview\": false, \"show_subscription_cta\": true}'::jsonb "
                    "WHERE id = %s",
                    (report_id,),
                )
            conn.commit()

        # 4. Extract key signals from the report for a personalized email
        owner_rank: int | None = None
        owner_reviews: int | None = None
        owner_rating: float | None = None
        key_comp_name: str | None = None
        review_gap: int | None = None
        report_comp_names: list[str] = []

        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT sections FROM generated_reports WHERE id = %s", (report_id,))
                    rpt_row = cur.fetchone()
                    rpt_sec = rpt_row[0] if (rpt_row and rpt_row[0]) else {}

            sov_rows_raw = (rpt_sec.get("share_of_voice") or {}).get("rows") or []

            # Pull competitor names from the actual report (not the prospect record)
            report_comp_names = [
                r.get("competitor_name") or r.get("name", "")
                for r in sov_rows_raw
                if not r.get("is_business") and (r.get("competitor_name") or r.get("name"))
            ]

            owner_row = next((r for r in sov_rows_raw if r.get("is_business")), None)
            if owner_row:
                owner_rank = int(owner_row.get("rank") or 0) or None
                owner_reviews = int(owner_row.get("reviews_total") or owner_row.get("review_count") or 0) or None
                _rating = owner_row.get("google_rating")
                owner_rating = float(_rating) if _rating else None

            if owner_rank == 1:
                key_row = next((r for r in sov_rows_raw if not r.get("is_business") and r.get("rank") == 2), None)
            else:
                key_row = next((r for r in sov_rows_raw if not r.get("is_business") and r.get("rank") == 1), None)
            if key_row:
                key_comp_name = key_row.get("competitor_name") or key_row.get("name")
                key_reviews = int(key_row.get("reviews_total") or key_row.get("review_count") or 0)
                if owner_reviews:
                    review_gap = abs(owner_reviews - key_reviews)
        except Exception as _e:
            import traceback; traceback.print_exc()  # log but don't crash

        # 5. Build personalized draft email
        # Use competitor names from the actual report; fall back to stored list
        comp_names_for_email = (report_comp_names or competitor_names)[:3]
        if len(comp_names_for_email) == 1:
            comp_str = comp_names_for_email[0]
        elif len(comp_names_for_email) == 2:
            comp_str = f"{comp_names_for_email[0]} and {comp_names_for_email[1]}"
        else:
            comp_str = f"{comp_names_for_email[0]}, {comp_names_for_email[1]}, and {comp_names_for_email[2]}"

        rating_str = f"★{owner_rating:.1f}" if owner_rating else ""

        # Subject
        draft_subject = f"Your local competitive report — {business_name}"

        # Opening line — specific to their position
        if owner_rank == 1 and key_comp_name and review_gap and owner_reviews:
            position_line = (
                f"You're leading your local market with {owner_reviews} reviews"
                f" and a {review_gap}-review gap over your closest competitor."
                f" I put together a full competitive report — attached."
            )
        elif owner_rank and owner_rank > 1 and key_comp_name and review_gap and owner_reviews:
            position_line = (
                f"You're sitting at #{owner_rank} in your local market with {owner_reviews} reviews"
                f" — {review_gap} reviews behind {key_comp_name}."
                f" I put together a full competitive report — attached."
            )
        else:
            position_line = f"I put together a full competitive report for {business_name} — attached."

        draft_body = (
            f"Hello,\n\n"
            f"{position_line}\n\n"
            f"It covers {comp_str}: review standings, ratings, and the complaint patterns showing up before customers decide who to choose.\n\n"
            f"If you want to swap in different competitors or have any questions, just reply.\n\n"
            f"Craig\n"
            f"pulselci.com"
        )

        # 6. Update prospect to ready
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE targeted_prospects
                       SET status = 'ready', report_id = %s,
                           draft_subject = %s, draft_body = %s, updated_at = NOW()
                       WHERE id = %s""",
                    (report_id, draft_subject, draft_body, prospect_id),
                )
            conn.commit()

        _gen_jobs[prospect_id] = {"status": "done", "error": None, "report_id": report_id}

    except Exception as exc:
        import traceback
        traceback.print_exc()
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE targeted_prospects SET status = 'error', updated_at = NOW() WHERE id = %s",
                    (prospect_id,),
                )
            conn.commit()
        _gen_jobs[prospect_id] = {"status": "error", "error": str(exc), "report_id": None}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/{prospect_id}/regenerate")
def regenerate_report(prospect_id: str, background_tasks: BackgroundTasks) -> dict:
    """Regenerate the report for an existing prospect (replaces old report_id)."""
    prospect = _get_targeted(prospect_id)
    if prospect["status"] not in ("ready", "error", "sent"):
        raise HTTPException(status_code=409, detail=f"Cannot regenerate from status '{prospect['status']}'")

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE targeted_prospects SET status = 'generating', report_id = NULL, updated_at = NOW() WHERE id = %s",
                (prospect_id,),
            )
        conn.commit()

    _gen_jobs[prospect_id] = {"status": "generating", "error": None, "report_id": None}
    background_tasks.add_task(_run_generate_bg, prospect_id)
    return {"ok": True, "status": "generating"}


@router.post("/{prospect_id}/mark-replied")
def mark_replied(prospect_id: str) -> dict:
    """Stop follow-up sequence for a prospect that has replied."""
    _get_targeted(prospect_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE targeted_prospects SET replied_at = NOW(), updated_at = NOW() WHERE id = %s",
                (prospect_id,),
            )
        conn.commit()
    return {"ok": True}


@router.post("/{prospect_id}/unmark-replied")
def unmark_replied(prospect_id: str) -> dict:
    """Re-enable follow-ups for a prospect."""
    _get_targeted(prospect_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE targeted_prospects SET replied_at = NULL, updated_at = NOW() WHERE id = %s",
                (prospect_id,),
            )
        conn.commit()
    return {"ok": True}


@router.delete("/{prospect_id}")
def delete_prospect