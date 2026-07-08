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
from pydantic import BaseModel

from app.core.db import get_conn
from app.services.email_service import send_plain_email
from app.services.place_resolver import suggest_competitors

router = APIRouter(prefix="/targeted", tags=["targeted"])

# In-memory job tracker: prospect_id -> {status, error, report_id}
_gen_jobs: dict[str, dict] = {}


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

        # 3. Mark as full (not blurred)
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE generated_reports "
                    "SET sections = sections || '{\"is_free_preview\": false}'::jsonb "
                    "WHERE id = %s",
                    (report_id,),
                )
            conn.commit()

        # 4. Build draft email
        comp_list = competitor_names[:3]
        if len(comp_list) == 1:
            comp_str = comp_list[0]
        elif len(comp_list) == 2:
            comp_str = f"{comp_list[0]} and {comp_list[1]}"
        else:
            comp_str = f"{comp_list[0]}, {comp_list[1]}, and {comp_list[2]}"

        draft_subject = f"competitive snapshot for {business_name}"
        draft_body = (
            f"Hi,\n\n"
            f"I put together a competitive intelligence report for {business_name} and wanted to get your take on it.\n\n"
            f"It breaks down how you stack up against {comp_str} on Google reviews, ratings, and local market visibility"
            f" -- the signals customers look at before picking between you and a competitor.\n\n"
            f"Take a look at the attachment and let me know what you think. "
            f"If there are other competitors you'd want to see in the comparison, just reply and I'll pull an updated version same day.\n\n"
            f"Craig\n"
            f"pulselci.com"
        )

        # 5. Update prospect to ready
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

@router.get("/followup-stats")
def followup_stats() -> list:
    """Return sent targeted prospects with follow-up status for the dashboard."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, business_name, contact_email, city, state,
                       competitor_names, sent_at, followup1_sent_at, followup2_sent_at
                FROM targeted_prospects
                WHERE status = 'sent'
                ORDER BY sent_at DESC
                LIMIT 200
            """)
            rows = cur.fetchall()

    result = []
    for row in rows:
        d = dict(row)
        d["id"] = str(d["id"])
        if d.get("competitor_names"):
            d["competitor_names"] = list(d["competitor_names"])
        result.append(d)
    return result


@router.post("/find-email")
def find_email(body: CreateProspectIn) -> dict:
    """
    Resolve the business website via Google Places then scrape / Hunter-lookup
    for the best contact email. Returns {email, website} or {email: null}.
    """
    import sys
    from pathlib import Path

    # Make sure outreach package is importable
    backend_dir = Path(__file__).resolve().parent.parent.parent
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))

    from app.services.place_resolver import resolve_place_id
    from outreach.discover import get_place_details, scrape_email_from_website, lookup_email_hunter

    place = resolve_place_id(body.business_name, body.city, body.state or "")
    if not place:
        return {"email": None, "website": None}

    details = get_place_details(place.place_id) or {}
    website = details.get("website")
    if not website:
        return {"email": None, "website": None}

    email = scrape_email_from_website(website)
    if not email:
        from urllib.parse import urlparse
        domain = urlparse(website).netloc.lstrip("www.")
        email = lookup_email_hunter(domain)

    return {"email": email, "website": website}


@router.post("/prospect")
def create_prospect(body: CreateProspectIn) -> dict:
    """
    Create a targeted prospect and return auto-suggested competitors.
    """
    if not body.business_name.strip():
        raise HTTPException(status_code=400, detail="Business name is required")

    # Suggest competitors (non-blocking — if it fails we return empty list)
    try:
        suggested = suggest_competitors(body.business_name, body.city, body.state)
    except Exception:
        suggested = []

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO targeted_prospects
                   (business_name, contact_email, city, state, status)
                   VALUES (%s, %s, %s, %s, 'pending_competitors')
                   RETURNING id""",
                (
                    body.business_name.strip(),
                    body.contact_email.strip() if body.contact_email else None,
                    body.city.strip(),
                    body.state.strip().upper(),
                ),
            )
            prospect_id = str(cur.fetchone()["id"])
        conn.commit()

    return {"id": prospect_id, "suggested_competitors": suggested}


@router.post("/{prospect_id}/confirm")
def confirm_competitors(prospect_id: str, body: ConfirmCompetitorsIn, background_tasks: BackgroundTasks) -> dict:
    """
    Confirm competitor list and kick off report generation in the background.
    """
    competitors = [n.strip() for n in body.competitor_names if n.strip()]
    if not competitors:
        raise HTTPException(status_code=400, detail="At least one competitor required")

    prospect = _get_targeted(prospect_id)
    if prospect["status"] not in ("pending_competitors", "error"):
        raise HTTPException(status_code=409, detail=f"Cannot confirm from status '{prospect['status']}'")

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE targeted_prospects
                   SET competitor_names = %s, status = 'generating', updated_at = NOW()
                   WHERE id = %s""",
                (competitors, prospect_id),
            )
        conn.commit()

    background_tasks.add_task(_run_generate_bg, prospect_id)
    return {"ok": True, "status": "generating"}


@router.get("/{prospect_id}/status")
def generation_status(prospect_id: str) -> dict:
    """Poll for report generation completion."""
    job = _gen_jobs.get(prospect_id)
    if not job:
        # Check DB status directly for jobs that survived a restart
        p = _get_targeted(prospect_id)
        return {"status": p["status"], "error": None, "report_id": str(p["report_id"]) if p.get("report_id") else None}
    return job


@router.get("/prospects")
def list_prospects(status: Optional[str] = None, limit: int = 100) -> list:
    """List targeted prospects, optionally filtered by status."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            if status:
                cur.execute(
                    "SELECT * FROM targeted_prospects WHERE status = %s ORDER BY created_at DESC LIMIT %s",
                    (status, limit),
                )
            else:
                cur.execute(
                    "SELECT * FROM targeted_prospects ORDER BY created_at DESC LIMIT %s",
                    (limit,),
                )
            rows = cur.fetchall()

    result = []
    for row in rows:
        d = dict(row)
        d["id"] = str(d["id"])
        if d.get("report_id"):
            d["report_id"] = str(d["report_id"])
        if d.get("competitor_names"):
            d["competitor_names"] = list(d["competitor_names"])
        result.append(d)
    return result


@router.post("/{prospect_id}/approve")
def approve_and_send(prospect_id: str) -> dict:
    """Render PDF from stored report and send email to prospect."""
    prospect = _get_targeted(prospect_id)
    if prospect["status"] != "ready":
        raise HTTPException(status_code=409, detail=f"Prospect not ready (status: {prospect['status']})")

    report_id = prospect.get("report_id")
    contact_email = prospect.get("contact_email")
    if not report_id:
        raise HTTPException(status_code=400, detail="No report generated yet")
    if not contact_email:
        raise HTTPException(status_code=400, detail="No contact email on this prospect")

    # Render PDF
    try:
        from app.api.generated_reports import _fetch_report
        from app.services.pdf_service import render_report_pdf
        report = _fetch_report(UUID(str(report_id)))
        pdf_bytes = render_report_pdf(report)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"PDF render failed: {exc}")

    business_name = prospect["business_name"]
    subject = prospect.get("draft_subject") or f"competitive snapshot for {business_name}"
    body = prospect.get("draft_body") or ""

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tf:
        tf.write(pdf_bytes)
        tmp_path = tf.name

    try:
        fname = business_name.replace(" ", "_").replace("/", "-") + "_LCI_Report.pdf"
        send_result = send_plain_email(
            to_email=contact_email,
            subject=subject,
            body=body,
            attachment_path=tmp_path,
            attachment_filename=fname,
            in_reply_to=None,
        )
    finally:
        os.unlink(tmp_path)

    if not send_result.ok:
        raise HTTPException(status_code=500, detail=f"Email send failed: {send_result.error}")

    # NOTE: intentionally NOT calling log_report_delivery here —
    # targeted prospects have their own follow-up sequence tracked on targeted_prospects,
    # separate from the free-report Day-5/12/21 sequence.

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE targeted_prospects SET status = 'sent', sent_at = NOW(), updated_at = NOW() WHERE id = %s",
                (prospect_id,),
            )
        conn.commit()

    return {"ok": True, "sent_to": contact_email}


@router.post("/{prospect_id}/skip")
def skip_prospect(prospect_id: str) -> dict:
    """Skip a targeted prospect."""
    _get_targeted(prospect_id)  # 404 guard
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE targeted_prospects SET status = 'skipped', updated_at = NOW() WHERE id = %s",
                (prospect_id,),
            )
        conn.commit()
    return {"ok": True}


@router.put("/{prospect_id}/draft")
def update_draft(prospect_id: str, body: dict) -> dict:
    """Update draft subject/body/email before sending."""
    _get_targeted(prospect_id)
    fields = {}
    if "draft_subject" in body:
        fields["draft_subject"] = body["draft_subject"]
    if "draft_body" in body:
        fields["draft_body"] = body["draft_body"]
    if "contact_email" in body:
        fields["contact_email"] = body["contact_email"]
    if not fields:
        return {"ok": True}

    set_clause = ", ".join(f"{k} = %s" for k in fields)
    values = list(fields.values()) + [prospect_id]
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE targeted_prospects SET {set_clause}, updated_at = NOW() WHERE id = %s",
                values,
            )
        conn.commit()
    return {"ok": True}


@router.delete("/{prospect_id}")
def delete_prospect(prospect_id: str) -> dict:
    """Delete a targeted prospect."""
    _get_targeted(prospect_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM targeted_prospects WHERE id = %s", (prospect_id,))
        conn.commit()
    return {"ok": True}
