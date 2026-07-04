"""
Outreach approval queue API.

Endpoints used by the approval UI to list, edit, approve, skip, and send
cold outreach emails to discovered prospects.
"""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path
from typing import Optional, List
from uuid import UUID

from fastapi import APIRouter, HTTPException, BackgroundTasks
from fastapi.responses import Response, RedirectResponse
from pydantic import BaseModel

from app.core.db import get_conn
from app.services.email_service import send_plain_email, log_report_delivery

router = APIRouter(prefix="/outreach", tags=["outreach"])

# Track discovery job status in memory
_discovery_status: dict = {"running": False, "last": None, "log": []}

# Track send-report jobs: prospect_id -> {status, error, report_id}
_send_report_jobs: dict[str, dict] = {}


def _run_discovery(
    city: str,
    state: str,
    categories: List[str],
    prospect_type: str = "local_business",
    partnership_type: str = "both",
) -> None:
    """Run prospect discovery in a background thread."""
    global _discovery_status
    _discovery_status["running"] = True
    _discovery_status["log"] = [f"Starting discovery: {city}, {state} — {', '.join(categories)}"]

    try:
        backend_dir = Path(__file__).resolve().parent.parent.parent
        if str(backend_dir) not in sys.path:
            sys.path.insert(0, str(backend_dir))

        from outreach.discover import discover
        discover(
            city=city,
            state=state,
            categories=categories,
            prospect_type=prospect_type,
            partnership_type=partnership_type,
        )
        _discovery_status["last"] = f"Done — {city}, {state}: {', '.join(categories)}"
        _discovery_status["log"].append("Discovery completed successfully.")
    except Exception as e:
        _discovery_status["last"] = f"Error: {e}"
        _discovery_status["log"].append(f"Error: {e}")
    finally:
        _discovery_status["running"] = False


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class ProspectOut(BaseModel):
    id: str
    business_name: str
    category: Optional[str]
    address: Optional[str]
    city: Optional[str]
    state: Optional[str]
    website: Optional[str]
    phone: Optional[str]
    contact_email: Optional[str]
    reviews_count: Optional[int]
    rating: Optional[float]
    top_competitor_name: Optional[str]
    top_competitor_reviews: Optional[int]
    draft_subject: Optional[str]
    draft_body: Optional[str]
    status: str
    created_at: str


class DraftUpdateIn(BaseModel):
    contact_email: Optional[str] = None
    draft_subject: Optional[str] = None
    draft_body: Optional[str] = None
    notes: Optional[str] = None
    ab_group: Optional[str] = None          # 'A' or 'B'
    ab_subject_label: Optional[str] = None  # e.g. 'A-S1', 'B-S4'


class DiscoverIn(BaseModel):
    city: str
    state: str
    categories: str  # comma-separated
    prospect_type: str = "local_business"  # local_business | agency
    partnership_type: str = "both"         # reseller | referral | both (agencies only)


class AgencyIn(BaseModel):
    business_name: str
    contact_email: Optional[str] = None
    website: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    partnership_type: str = "both"  # reseller | referral | both
    notes: Optional[str] = None
    draft_subject: Optional[str] = None
    draft_body: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require_admin_key(api_key: str | None) -> None:
    expected = os.getenv("ADMIN_API_KEY", "")
    if expected and api_key != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


def _get_prospect(prospect_id: str) -> dict:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM outreach_prospects WHERE id = %s",
                (prospect_id,),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Prospect not found")
            return dict(row)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/discover")
def start_discovery(body: DiscoverIn, background_tasks: BackgroundTasks) -> dict:
    """Trigger prospect discovery in the background."""
    if _discovery_status["running"]:
        raise HTTPException(status_code=409, detail="A discovery run is already in progress. Check /outreach/discover/status.")

    categories = [c.strip() for c in body.categories.split(",") if c.strip()]
    if not categories:
        raise HTTPException(status_code=400, detail="At least one category is required.")

    background_tasks.add_task(
        _run_discovery,
        city=body.city,
        state=body.state,
        categories=categories,
        prospect_type=body.prospect_type,
        partnership_type=body.partnership_type,
    )
    return {"ok": True, "message": f"Discovery started for {body.city}, {body.state}. Check the queue in a few minutes."}


@router.get("/discover/status")
def discovery_status() -> dict:
    """Check whether a discovery run is in progress."""
    return {
        "running": _discovery_status["running"],
        "last": _discovery_status["last"],
        "log": _discovery_status["log"][-10:],
    }


@router.post("/clear")
def clear_queue() -> dict:
    """Delete all prospects except sent/converted records."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM outreach_prospects WHERE status NOT IN ('sent', 'converted')"
            )
            deleted = cur.rowcount
        conn.commit()
    return {"ok": True, "deleted": deleted}


@router.get("/queue")
def list_queue(status: str = "draft_ready", limit: int = 50, search: str = "") -> list[dict]:
    """List prospects in the approval queue, flagging any previously contacted."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            if search:
                pattern = f"%{search}%"
                cur.execute(
                    """
                    SELECT
                        p.id, p.business_name, p.category, p.address, p.city, p.state,
                        p.website, p.phone, p.contact_email, p.reviews_count, p.rating,
                        p.top_competitor_name, p.top_competitor_reviews,
                        p.draft_subject, p.draft_body, p.status,
                        p.created_at::text,
                        EXISTS (
                            SELECT 1 FROM outreach_prospects prev
                            WHERE prev.place_id = p.place_id
                              AND prev.status IN ('sent', 'converted')
                              AND prev.id != p.id
                        ) AS previously_contacted
                    FROM outreach_prospects p
                    WHERE p.status = %s
                      AND (p.business_name ILIKE %s OR p.contact_email ILIKE %s)
                    ORDER BY
                        CASE WHEN %s = 'sent' THEN p.sent_at END DESC NULLS LAST,
                        CASE WHEN %s != 'sent' THEN p.reviews_count END DESC NULLS LAST
                    LIMIT %s
                    """,
                    (status, pattern, pattern, status, status, limit),
                )
            else:
                cur.execute(
                    """
                    SELECT
                        p.id, p.business_name, p.category, p.address, p.city, p.state,
                        p.website, p.phone, p.contact_email, p.reviews_count, p.rating,
                        p.top_competitor_name, p.top_competitor_reviews,
                        p.draft_subject, p.draft_body, p.status,
                        p.created_at::text,
                        EXISTS (
                            SELECT 1 FROM outreach_prospects prev
                            WHERE prev.place_id = p.place_id
                              AND prev.status IN ('sent', 'converted')
                              AND prev.id != p.id
                        ) AS previously_contacted
                    FROM outreach_prospects p
                    WHERE p.status = %s
                    ORDER BY
                        CASE WHEN %s = 'sent' THEN p.sent_at END DESC NULLS LAST,
                        CASE WHEN %s != 'sent' THEN p.reviews_count END DESC NULLS LAST
                    LIMIT %s
                    """,
                    (status, status, status, limit),
                )
            rows = cur.fetchall()
            return [dict(r) for r in rows]


@router.get("/stats")
def get_stats() -> dict:
    """Return counts by status."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT status, COUNT(*) as count
                FROM outreach_prospects
                GROUP BY status
                ORDER BY count DESC
                """
            )
            rows = cur.fetchall()
            return {r["status"]: r["count"] for r in rows}


@router.get("/summary")
def get_summary() -> dict:
    """
    Returns total prospect counts and email-sent counts broken down by type
    (business vs agency). Powers the follow-up dashboard stat cards.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    COALESCE(prospect_type, 'local_business') AS ptype,
                    COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE status IN ('sent', 'converted')) AS emailed,
                    COUNT(*) FILTER (WHERE status = 'converted') AS converted
                FROM outreach_prospects
                WHERE COALESCE(is_test, false) = false
                GROUP BY ptype
                """
            )
            rows = cur.fetchall()
            result = {}
            for r in rows:
                result[r["ptype"]] = {
                    "total":     r["total"],
                    "emailed":   r["emailed"],
                    "converted": r["converted"],
                }
            return result


@router.patch("/{prospect_id}/draft")
def update_draft(prospect_id: str, body: DraftUpdateIn) -> dict:
    """Edit a prospect's email, contact email, or notes before approving."""
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    set_clause = ", ".join(f"{k} = %s" for k in updates)
    values = list(updates.values()) + [prospect_id]

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE outreach_prospects SET {set_clause}, updated_at = NOW() WHERE id = %s",
                values,
            )
        conn.commit()
    return {"ok": True}


@router.post("/{prospect_id}/skip")
def skip_prospect(prospect_id: str) -> dict:
    """Mark a prospect as skipped."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE outreach_prospects SET status = 'skipped', updated_at = NOW() WHERE id = %s",
                (prospect_id,),
            )
        conn.commit()
    return {"ok": True}


@router.post("/{prospect_id}/approve")
def approve_and_send(prospect_id: str) -> dict:
    """
    Approve a prospect and immediately send the draft email.
    Requires contact_email and draft_body to be set.
    """
    prospect = _get_prospect(prospect_id)

    to_email = prospect.get("contact_email")
    subject = prospect.get("draft_subject") or f"Competitive snapshot for {prospect['business_name']}"
    body = prospect.get("draft_body")

    if not to_email:
        raise HTTPException(status_code=400, detail="No contact_email set — add one before approving")
    if not body:
        raise HTTPException(status_code=400, detail="No draft_body set")

    result = send_plain_email(
        to_email=to_email,
        subject=subject,
        body=body,
        tracking_id=prospect_id,
    )

    if not result.ok:
        raise HTTPException(status_code=500, detail=f"Email send failed: {result.error}")

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE outreach_prospects
                SET status = 'sent', approved_at = NOW(), sent_at = NOW(),
                    updated_at = NOW(), message_id = %s
                WHERE id = %s
                """,
                (result.message_id, prospect_id),
            )
        conn.commit()

    return {"ok": True, "sent_to": to_email, "message_id": result.message_id}


@router.post("/agency")
def add_agency(body: AgencyIn) -> dict:
    """Manually add an agency to the outreach pipeline."""
    import uuid
    synthetic_place_id = f"agency_{uuid.uuid4().hex}"

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO outreach_prospects (
                    place_id, business_name, website, city, state,
                    contact_email, draft_subject, draft_body, notes,
                    prospect_type, partnership_type, status
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'agency', %s, 'draft_ready')
                RETURNING id
                """,
                (
                    synthetic_place_id, body.business_name, body.website,
                    body.city, body.state, body.contact_email,
                    body.draft_subject, body.draft_body, body.notes,
                    body.partnership_type,
                ),
            )
            row = cur.fetchone()
        conn.commit()
    return {"ok": True, "id": str(row["id"])}


@router.get("/agencies")
def list_agencies(status: str = "all", limit: int = 100) -> list[dict]:
    """List agency prospects (prospect_type='agency')."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            if status == "all":
                cur.execute(
                    """
                    SELECT id, business_name, website, city, state,
                           contact_email, draft_subject, draft_body, notes,
                           partnership_type, status,
                           created_at::text, sent_at::text
                    FROM outreach_prospects
                    WHERE prospect_type = 'agency'
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
            else:
                cur.execute(
                    """
                    SELECT id, business_name, website, city, state,
                           contact_email, draft_subject, draft_body, notes,
                           partnership_type, status,
                           created_at::text, sent_at::text
                    FROM outreach_prospects
                    WHERE prospect_type = 'agency' AND status = %s
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (status, limit),
                )
            return [dict(r) for r in cur.fetchall()]


@router.get("/all")
def list_all(limit: int = 200) -> list[dict]:
    """List all prospects across all statuses (for the full pipeline view)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, business_name, category, city, state, contact_email,
                       reviews_count, rating, status, created_at::text, sent_at::text,
                       email_opened_at::text, email_open_count, link_clicked_at::text
                FROM outreach_prospects
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (limit,),
            )
            return [dict(r) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# Email tracking — open pixel and click redirect
# ---------------------------------------------------------------------------

_TRACKING_PIXEL = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00"
    b"!\xf9\x04\x00\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
)


@router.get("/track/open/{prospect_id}")
def track_open(prospect_id: str):
    """Open tracking pixel — returns transparent GIF and logs the open."""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE outreach_prospects
                    SET
                        email_open_count = email_open_count + 1,
                        email_opened_at  = COALESCE(email_opened_at, NOW()),
                        updated_at       = NOW()
                    WHERE id = %s
                    """,
                    (prospect_id,),
                )
            conn.commit()
    except Exception as e:
        print(f"[TRACK OPEN] error for {prospect_id}: {e}")

    return Response(content=_TRACKING_PIXEL, media_type="image/gif")


@router.get("/track/click/{prospect_id}")
def track_click(prospect_id: str, url: str = "https://pulselci.com"):
    """Click tracking redirect — logs first click then redirects to url."""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE outreach_prospects
                    SET
                        link_clicked_at = COALESCE(link_clicked_at, NOW()),
                        updated_at      = NOW()
                    WHERE id = %s
                    """,
                    (prospect_id,),
                )
            conn.commit()
    except Exception as e:
        print(f"[TRACK CLICK] error for {prospect_id}: {e}")

    return RedirectResponse(url=url, status_code=302)


# ---------------------------------------------------------------------------
# Send-report-as-reply — generates a full report and sends it in-thread
# ---------------------------------------------------------------------------

class SendReportIn(BaseModel):
    competitor_names: List[str]


def _run_send_report_bg(prospect_id: str, prospect: dict, competitor_names: list) -> None:
    """
    Background job: onboard prospect, generate report, send as reply in cold email thread.
    Status tracked in _send_report_jobs[prospect_id].
    """
    import os
    import tempfile
    from uuid import UUID

    _send_report_jobs[prospect_id] = {"status": "generating", "error": None, "report_id": None}

    try:
        # 1. Create/find business + resolve Place IDs + collect snapshots/reviews synchronously
        from app.services.prospect_onboarding_service import onboard_prospect
        result = onboard_prospect(
            contact_name="",
            contact_email=prospect["contact_email"],
            business_name=prospect["business_name"],
            city=prospect.get("city") or "",
            state=prospect.get("state") or "",
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

        # 3. Mark as full (not blurred free preview)
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE generated_reports "
                    "SET sections = sections || '{\"is_free_preview\": false}'::jsonb "
                    "WHERE id = %s",
                    (report_id,),
                )
            conn.commit()

        # 4. Render PDF
        from app.api.generated_reports import _fetch_report
        from app.services.pdf_service import render_report_pdf
        report = _fetch_report(UUID(report_id))
        pdf_bytes = render_report_pdf(report)

        # 5. Build personal email body
        business_name = prospect["business_name"]
        orig_subject = (prospect.get("draft_subject") or f"competitive report for {business_name}").strip()
        has_thread = bool(prospect.get("message_id"))
        email_subject = f"Re: {orig_subject}" if has_thread else orig_subject
        email_body = (
            f"Hi,\n\n"
            f"As promised, here is the competitive intelligence report for {business_name}. "
            f"It covers where you stand on reviews, ratings, and local market positioning "
            f"vs your competitors.\n\n"
            f"Happy to walk through anything in there. Just reply here.\n\n"
            f"If you would like this updated monthly, you can subscribe at pulselci.com/#pricing. "
            f"$99/month, cancel anytime.\n\n"
            f"Craig\n"
            f"Pulse LCI"
        )

        # 6. Write PDF to temp file and send via craig@ as threaded reply
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tf:
            tf.write(pdf_bytes)
            tmp_path = tf.name

        try:
            fname = business_name.replace(" ", "_").replace("/", "-") + "_LCI_Report.pdf"
            send_result = send_plain_email(
                to_email=prospect["contact_email"],
                subject=email_subject,
                body=email_body,
                attachment_path=tmp_path,
                attachment_filename=fname,
                in_reply_to=prospect.get("message_id"),
            )
        finally:
            os.unlink(tmp_path)

        if not send_result.ok:
            raise RuntimeError(f"Email send failed: {send_result.error}")

        # 7. Log delivery so post-report follow-up sequence (Day-5/12/21) fires
        log_report_delivery(
            report_id=report_id,
            recipient_email=prospect["contact_email"],
            status="sent",
        )

        # 8. Mark prospect converted — stops cold follow-up chain
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE outreach_prospects SET status = 'converted', updated_at = NOW() WHERE id = %s",
                    (prospect_id,),
                )
            conn.commit()

        _send_report_jobs[prospect_id] = {"status": "done", "error": None, "report_id": report_id}

    except Exception as exc:
        import traceback
        traceback.print_exc()
        _send_report_jobs[prospect_id] = {"status": "error", "error": str(exc), "report_id": None}


@router.post("/{prospect_id}/send-report")
def send_report_reply(prospect_id: str, body: SendReportIn, background_tasks: BackgroundTasks) -> dict:
    """
    Generate and send a full competitive report as a threaded reply to the original cold email