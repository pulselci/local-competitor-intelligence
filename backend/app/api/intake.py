"""
Prospect intake endpoint.

Receives the free-report request form submission from pulselci.com,
kicks off the onboarding pipeline in the background, and immediately
redirects the user to the thank-you page.

HTML form should POST to: POST /intake/prospect
with Content-Type: application/x-www-form-urlencoded

Expected form fields (matching index.html field names):
  name        — contact full name
  email       — contact email
  phone       — contact phone (optional)
  business    — business name
  city        — city
  state       — state
  competitor1 — competitor name (required)
  competitor2 — competitor name (optional)
  competitor3 — competitor name (optional)
"""
from __future__ import annotations

import logging
import os
import threading

from fastapi import APIRouter, Form
from fastapi.responses import RedirectResponse

from app.services.prospect_onboarding_service import onboard_prospect

logger = logging.getLogger(__name__)

router = APIRouter(tags=["intake"])

# Where to send the user after form submission.
# Override via THANK_YOU_URL env var on Render.
THANK_YOU_URL = os.getenv("THANK_YOU_URL", "https://pulselci.com/thank-you")


@router.post("/intake/prospect", include_in_schema=False)
def intake_prospect(
    name: str = Form(""),
    email: str = Form(""),
    phone: str = Form(""),
    business: str = Form(""),
    city: str = Form(""),
    state: str = Form(""),
    competitor1: str = Form(""),
    competitor2: str = Form(""),
    competitor3: str = Form(""),
):
    """
    Receives the free-report intake form, fires off the onboarding
    pipeline in a background thread, and immediately redirects to
    the thank-you page so the user never waits.
    """
    contact_name = name.strip()
    contact_email = email.strip()
    contact_phone = phone.strip()
    business_name = business.strip()
    city_clean = city.strip()
    state_clean = state.strip()

    competitor_names = [
        c.strip()
        for c in [competitor1, competitor2, competitor3]
        if c.strip()
    ]

    logger.info(
        "Intake received: business=%r city=%r state=%r email=%r competitors=%r",
        business_name, city_clean, state_clean, contact_email, competitor_names,
    )

    # Run the full pipeline in a background thread so the HTTP response
    # returns immediately. The prospect sees the thank-you page right away
    # and receives the report by email once processing completes.
    def _run():
        onboard_prospect(
            contact_name=contact_name,
            contact_email=contact_email,
            contact_phone=contact_phone,
            business_name=business_name,
            city=city_clean,
            state=state_clean,
            competitor_names=competitor_names,
        )

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    return RedirectResponse(url=THANK_YOU_URL, status_code=303)
