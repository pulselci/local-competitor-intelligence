from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.generated_reports import router as generated_reports_router
from app.api.intake import router as intake_router
from app.api.outreach import router as outreach_router
from app.api.targeted import router as targeted_router
from app.api.routes import router as api_router
from app.core.db import close_pool, get_conn

app = FastAPI(
    title="Local Competitor Intelligence (Phase 1)",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # allow all for now (safe for MVP)
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Existing API routes
app.include_router(api_router)

# Generated report PDF routes
app.include_router(generated_reports_router)

# Prospect intake (free report request form)
app.include_router(intake_router)

# Outreach approval queue
app.include_router(outreach_router)

# Targeted outreach
app.include_router(targeted_router)


@app.get("/admin/onboarding", include_in_schema=False)
def onboarding_form():
    html_path = Path(__file__).resolve().parent / "static" / "onboarding.html"
    return FileResponse(html_path)


@app.get("/admin/dashboard", include_in_schema=False)
def stats_dashboard():
    html_path = Path(__file__).resolve().parent / "static" / "stats_dashboard.html"
    return FileResponse(html_path)


@app.get("/outreach/ui", include_in_schema=False)
def outreach_queue_ui():
    html_path = Path(__file__).resolve().parent / "static" / "outreach_queue.html"
    return FileResponse(html_path)


@app.get("/targeted/ui", include_in_schema=False)
def targeted_outreach_ui():
    html_path = Path(__file__).resolve().parent / "static" / "targeted_outreach.html"
    return FileResponse(html_path)


@app.get("/targeted/prospects-crm", include_in_schema=False)
def prospects_crm_ui():
    html_path = Path(__file__).resolve().parent / "static" / "prospects_crm.html"
    return FileResponse(html_path)


@app.get("/hub", include_in_schema=False)
@app.get("/admin/hub", include_in_schema=False)
def command_hub():
    """Unified navigation hub."""
    hub_path = Path(__file__).resolve().parent / "static" / "hub.html"
    return FileResponse(hub_path)


@app.get("/logo.png", include_in_schema=False)
def serve_logo():
    logo_path = Path(__file__).resolve().parent / "static" / "pulse-lci-logo.png"
    return FileResponse(logo_path, media_type="image/png")


@app.on_event("startup")
def on_startup():
    """Run idempotent DB migrations on every deploy."""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "ALTER TABLE outreach_prospects ADD COLUMN IF NOT EXISTS message_id TEXT"
                )
                cur.execute(
                    "ALTER TABLE targeted_prospects ADD COLUMN IF NOT EXISTS followup1_sent_at TIMESTAMPTZ"
                )
                cur.execute(
                    "ALTER TABLE targeted_prospects ADD COLUMN IF NOT EXISTS followup2_sent_at TIMESTAMPTZ"
                )
                cur.execute(
                    "ALTER TABLE targeted_prospects ADD COLUMN IF NOT EXISTS replied_at TIMESTAMPTZ"
                )
                cur.execute(
                    "ALTER TABLE targeted_prospects ADD COLUMN IF NOT EXISTS email_opened_at TIMESTAMPTZ"
                )
                cur.execute(
                    "ALTER TABLE targeted_prospects ADD COLUMN IF NOT EXISTS email_open_count INT DEFAULT 0"
                )
                cur.execute(
                    "ALTER TABLE targeted_prospects ADD COLUMN IF NOT EXISTS is_test BOOLEAN DEFAULT FALSE"
                )
                cur.execute(
                    "ALTER TABLE targeted_prospects ADD COLUMN IF NOT EXISTS message_id TEXT"
                )
                # Mark any records sent to Craig's own email as sandbox/test
                cur.execute(
                    "UPDATE targeted_prospects SET is_test = TRUE WHERE contact_email = 'craigw0503@gmail.com' AND is_test IS NOT TRUE"
                )
                cur.execute(
                    """CREATE TABLE IF NOT EXISTS targeted_prospects (
                        id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                        business_name   TEXT NOT NULL,
                        contact_email   TEXT,
                        city            TEXT,
                        state           TEXT,
                        competitor_names TEXT[] DEFAULT '{}',
                        status          TEXT NOT NULL DEFAULT 'pending_competitors',
                        report_id       UUID,
                        draft_subject   TEXT,
                        draft_body      TEXT,
                        message_id      TEXT,
                        sent_at         TIMESTAMPTZ,
                        created_at      TIMESTAMPTZ DEFAULT NOW(),
                        updated_at      TIMESTAMPTZ DEFAULT NOW()
                    )"""
                )
            conn.commit()
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("Startup migration warning: %s", e)


@app.on_event("shutdown")
def on_shutdown():
    close_pool()
