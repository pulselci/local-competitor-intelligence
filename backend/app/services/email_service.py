from __future__ import annotations

import os
import smtplib
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Optional
from uuid import UUID

from app.core.db import get_conn
from app.core.config import settings


@dataclass
class EmailSendResult:
    ok: bool
    error: str | None = None
    message_id: str | None = None  # Message-ID header (for threading)


def _has_stripe_customer(business_id: str) -> bool:
    """Returns True if the business is an active paying subscriber."""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT 1 FROM report_schedules
                    WHERE business_id = %s AND is_enabled = true
                    LIMIT 1
                    """,
                    (str(business_id),),
                )
                return cur.fetchone() is not None
    except Exception:
        return False


def _log_report_delivery(
    report_id: UUID | str | None,
    recipient_email: str,
    status: str,
    error: str | None = None,
) -> None:
    """
    Best-effort delivery log.
    Never raises back into email sending.
    """
    if not report_id:
        return

    sql = """
    insert into report_delivery_logs (
        report_id,
        recipient_email,
        status,
        error,
        sent_at
    )
    values (
        %s,
        %s,
        %s,
        %s,
        %s
    )
    """

    sent_at = datetime.now(timezone.utc) if status == "sent" else None

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql,
                    (
                        str(report_id),
                        recipient_email,
                        status,
                        error,
                        sent_at,
                    ),
                )
            conn.commit()
    except Exception as log_error:
        print(f"[report_delivery_logs] failed to write log: {log_error}")


def log_report_delivery(
    report_id: "UUID | str | None",
    recipient_email: str,
    status: str,
    error: str | None = None,
) -> None:
    """Public wrapper around _log_report_delivery for use outside this module."""
    _log_report_delivery(report_id=report_id, recipient_email=recipient_email, status=status, error=error)


def send_report_email(
    to_email: str,
    subject: str,
    body_text: str,
    pdf_bytes: bytes,
    filename: str | None = None,
    report_id: UUID | str | None = None,
    business_name: Optional[str] = None,
    summary_text: Optional[str] = None,
    business_id: Optional[str] = None,
) -> EmailSendResult:
    """
    Sends an email with a PDF attachment via SMTP.

    Env vars used:
      SMTP_HOST (default: smtp.gmail.com)
      SMTP_PORT (default: 587)
      SMTP_USER (required for real send)
      SMTP_PASS (required for real send)
      SMTP_FROM (optional; defaults to SMTP_USER)
      SMTP_TLS  (default: true)
      EMAIL_DRY_RUN (default: false)
    """
    dry_run = os.getenv("EMAIL_DRY_RUN", "false").strip().lower() in ("1", "true", "yes")

    host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    port = int(os.getenv("SMTP_PORT", "587"))

    user = (settings.SMTP_USER or "").strip()
    password = (settings.SMTP_PASS or "").strip()
    from_email = os.getenv("SMTP_FROM", "").strip() or user

    use_tls = os.getenv("SMTP_TLS", "true").strip().lower() in ("1", "true", "yes")

    if not to_email:
        return EmailSendResult(ok=False, error="to_email is required")

    if dry_run:
        print(f"[EMAIL_DRY_RUN] Would send to={to_email} subject={subject} bytes={len(pdf_bytes)}")
        _log_report_delivery(
            report_id=report_id,
            recipient_email=to_email,
            status="sent",
            error=None,
        )
        return EmailSendResult(ok=True)

    if not user or not password:
        result = EmailSendResult(ok=False, error="Missing SMTP_USER or SMTP_PASS")
        _log_report_delivery(
            report_id=report_id,
            recipient_email=to_email,
            status="failed",
            error=result.error,
        )
        return result

    if not from_email:
        result = EmailSendResult(ok=False, error="Missing SMTP_FROM or SMTP_USER")
        _log_report_delivery(
            report_id=report_id,
            recipient_email=to_email,
            status="failed",
            error=result.error,
        )
        return result

    display_business_name = business_name or "your business"

    headline = (summary_text or "").strip()
    if len(headline) > 220:
        headline = headline[:217] + "..."

    html_body = f"""
    <html>
    <body style="margin: 0; padding: 0; background: #f4f7fb; font-family: Arial, Helvetica, sans-serif; color: #172033;">
        <div style="max-width: 680px; margin: 0 auto; padding: 28px 20px;">
        <div style="background: #ffffff; border: 1px solid #dce6f5; border-radius: 14px; padding: 28px;">
            <div style="font-size: 11px; font-weight: 700; letter-spacing: 0.12em; text-transform: uppercase; color: #5c6f91; margin-bottom: 10px;">
            Pulse LCI
            </div>

            <h2 style="margin: 0 0 10px 0; font-size: 24px; line-height: 1.2; color: #122033;">
            Your latest competitive intelligence report is ready
            </h2>

            {f'''
            <div style="margin: 0 0 16px 0; padding: 14px 16px; background: #f8fbff; border: 1px solid #dce6f5; border-radius: 10px;">
            <div style="font-size: 11px; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; color: #62738f; margin-bottom: 6px;">
                Headline takeaway
            </div>
            <div style="font-size: 15px; line-height: 1.5; font-weight: 700; color: #122033;">
                {headline}
            </div>
            </div>
            ''' if headline else ''}

            <p style="margin: 0 0 14px 0; font-size: 14px; line-height: 1.6; color: #30415f;">
            Attached is your latest Pulse LCI report for <strong>{business_name or "your business"}</strong>.
            </p>

            <p style="margin: 0 0 18px 0; font-size: 14px; line-height: 1.6; color: #30415f;">
            This report highlights competitive movement in your local market, review momentum, positioning gaps, and the clearest next actions to take.
            </p>

            <p style="margin: 0; font-size: 14px; line-height: 1.6; color: #30415f;">
            Thanks,<br>
            <strong>Pulse LCI Reports</strong>
            </p>
        </div>

        <div style="text-align: center; margin-top: 20px; font-size: 11px; color: #8a9ab5; line-height: 1.6;">
            {f'''<a href="https://pulse-lci-api.onrender.com/billing/portal/{business_id}"
               style="color: #8a9ab5; text-decoration: underline;">
                Manage or cancel your subscription
            </a> &nbsp;·&nbsp; ''' if business_id and _has_stripe_customer(business_id) else ''}
            {f'<a href="https://pulse-lci-api.onrender.com/unsubscribe?id={business_id}&type=business" style="color:#8a9ab5;text-decoration:underline;">Unsubscribe from all Pulse LCI emails</a>' if business_id else '<span>To unsubscribe, reply with &quot;unsubscribe&quot; in the subject.</span>'}
            <br>
            <span>Pulse LCI &nbsp;·&nbsp; United States</span>
        </div>

        </div>
    </body>
    </html>
    """

    msg = EmailMessage()
    msg["From"] = f"Pulse LCI Reports <{from_email}>"
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body_text or "")
    msg.add_alternative(html_body, subtype="html")

    msg.add_attachment(
        pdf_bytes,
        maintype="application",
        subtype="pdf",
        filename=filename or "LCI_Report.pdf",
    )

    try:
        print(f"[EMAIL DEBUG] host={host} port={port} user={user} from={from_email} to={to_email} dry_run={dry_run} tls={use_tls}")
        with smtplib.SMTP(host, port, timeout=30) as smtp:
            smtp.ehlo()
            if use_tls:
                smtp.starttls()
                smtp.ehlo()
            smtp.login(user, password)
            smtp.send_message(msg)

        _log_report_delivery(
            report_id=report_id,
            recipient_email=to_email,
            status="sent",
            error=None,
        )
        return EmailSendResult(ok=True)

    except Exception as e:
        error_text = str(e)
        _log_report_delivery(
            report_id=report_id,
            recipient_email=to_email,
            status="failed",
            error=error_text,
        )
        return EmailSendResult(ok=False, error=error_text)

def send_plain_email(
    to_email: str,
    subject: str,
    body: str,
    from_name: str = "Craig",
    from_address: str = "craig@pulselci.com",
    attachment_path: str | None = None,
    attachment_filename: str | None = None,
    tracking_id: str | None = None,
    in_reply_to: str | None = None,
) -> "EmailSendResult":
    """
    Send a plain-text (+ HTML alternative) email, optionally with a file attachment.
    Outreach emails come from Craig personally (craig@pulselci.com).

    If tracking_id is provided (prospect UUID), the HTML version will include:
      - A 1x1 tracking pixel that logs opens via /outreach/track/open/{tracking_id}
      - Website links rewritten to /outreach/track/click/{tracking_id}?url=...
    """
    import re
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.mime.base import MIMEBase
    from email import encoders

    user = (settings.OUTREACH_SMTP_USER or settings.SMTP_USER or "").strip()
    password = (settings.OUTREACH_SMTP_PASS or settings.SMTP_PASS or "").strip()
    host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    port = int(os.getenv("SMTP_PORT", "587"))
    use_tls = os.getenv("SMTP_TLS", "true").strip().lower() in ("1", "true", "yes")
    dry_run = os.getenv("EMAIL_DRY_RUN", "false").strip().lower() in ("1", "true", "yes")

    api_base = os.getenv("API_BASE_URL", "https://pulse-lci-api.onrender.com").rstrip("/")

    resolved_from = user if user else from_address
    display_from = f"{from_name} <{resolved_from}>"

    if dry_run:
        print(f"[EMAIL DRY RUN] plain email from={display_from} to={to_email} subject={subject} attachment={attachment_path} tracking_id={tracking_id} in_reply_to={in_reply_to}")
        return EmailSendResult(ok=True, error=None, message_id="<dry-run@pulselci.com>")

    if not user or not password:
        return EmailSendResult(ok=False, error="Missing SMTP_USER or SMTP_PASS")

    def _make_html(plain: str, tid: str | None) -> str:
        import html as html_lib
        import urllib.parse

        # Convert plain text to basic HTML paragraphs
        paragraphs = plain.strip().split("\n\n")
        paras_html = "".join(
            f'<p style="margin:0 0 14px 0;font-size:14px;line-height:1.6;color:#172033;">'
            f'{html_lib.escape(p.strip()).replace(chr(10), "<br>")}</p>'
            for p in paragraphs if p.strip()
        )

        # Rewrite http(s) links to click-tracking redirects
        if tid:
            def _wrap_url(m: re.Match) -> str:
                encoded = urllib.parse.quote(m.group(0), safe="")
                return f"{api_base}/outreach/track/click/{tid}?url={encoded}"
            paras_html = re.sub(r"https?://[^\s<>\"']+", _wrap_url, paras_html)

        pixel = (
            f'<img src="{api_base}/outreach/track/open/{tid}" '
            f'width="1" height="1" style="display:block;width:1px;height:1px;border:0;" alt="" />'
            if tid else ""
        )

        return (
            f'<html><body style="margin:0;padding:0;background:#ffffff;'
            f'font-family:Arial,Helvetica,sans-serif;">'
            f'<div style="max-width:600px;margin:0 auto;padding:24px 20px;">'
            f'{paras_html}'
            f'</div>{pixel}</body></html>'
        )

    try:
        import email.utils
        msg = MIMEMultipart("mixed")
        msg["Subject"] = subject
        msg["From"] = display_from
        msg["Reply-To"] = display_from
        msg["To"] = to_email
        msg["Message-ID"] = email.utils.make_msgid(domain="pulselci.com")
        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
            msg["References"] = in_reply_to

        # Multipart/alternative: plain text + HTML (with tracking pixel)
        alt = MIMEMultipart("alternative")
        alt.attach(MIMEText(body, "plain"))
        alt.attach(MIMEText(_make_html(body, tracking_id), "html"))
        msg.attach(alt)

        if attachment_path and os.path.exists(attachment_path):
            with open(attachment_path, "rb") as f:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(f.read())
            encoders.encode_base64(part)
            fname = attachment_filename or os.path.basename(attachment_path)
            part.add_header("Content-Disposition", f'attachment; filename="{fname}"')
            msg.attach(part)

        with smtplib.SMTP(host, port) as server:
            if use_tls:
                server.starttls()
            server.login(user, password)
            server.sendmail(resolved_from, [to_email], msg.as_string())

        return EmailSendResult(ok=True, error=None, message_id=msg["Message-ID"])
    except Exception as e:
        print(f"[EMAIL] plain email failed to={to_email}: {e}")
        return EmailSendResult(ok=False, error=str(e))


def log_report_delivery(
    report_id: "UUID | str | None",
    recipient_email: str,
    status: str,
    error: str | None = None,
) -> None:
    """Public wrapper around _log_report_delivery for use outside this module."""
    _log_report_delivery(report_id=report_id, recipient_email=recipient_email, status=status, error=error)
