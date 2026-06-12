import logging
from datetime import datetime, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

from app.core.db import get_conn
from app.api.routes import generate_business_report
from app.api.generated_reports import send_generated_report_email, SendReportRequest

logger = logging.getLogger(__name__)


def _fetch_due_schedules(now: datetime) -> list[dict]:
    sql = """
    select
        rs.id,
        rs.business_id,
        rs.frequency,
        rs.day_of_month,
        rs.hour,
        rs.minute,
        rs.is_enabled,
        rs.timezone,
        rs.next_run_at
    from report_schedules rs
    join businesses b
      on b.id = rs.business_id
    where rs.next_run_at is not null
      and rs.next_run_at <= %s
      and coalesce(b.is_active, false) = true
    order by rs.next_run_at asc
    for update skip locked
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (now,))
            return [dict(r) for r in cur.fetchall()]


def _fetch_schedule_recipients(business_id: UUID) -> list[dict]:
    sql = """
    select email
    from report_recipients
    where business_id = %s
      and is_enabled = true
    order by email asc
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (str(business_id),))
            return [dict(r) for r in cur.fetchall()]


def _mark_schedule_run(schedule: dict, now: datetime) -> None:
    """
    Advance next_run_at using the schedule's configured monthly cadence,
    honoring the schedule's local timezone and storing the result in UTC.
    """
    from calendar import monthrange

    schedule_id = UUID(str(schedule["id"]))
    frequency = str(schedule.get("frequency") or "monthly").strip().lower()
    day_of_month = int(schedule.get("day_of_month") or 1)
    hour = int(schedule.get("hour") or 9)
    minute = int(schedule.get("minute") or 0)
    is_enabled = bool(schedule.get("is_enabled", True))
    timezone_name = str(schedule.get("timezone") or "America/New_York").strip()

    if not is_enabled:
        next_run_at_utc = None
    else:
        try:
            local_tz = ZoneInfo(timezone_name)
        except Exception:
            logger.warning(f"[scheduler] invalid timezone '{timezone_name}', falling back to America/New_York")
            local_tz = ZoneInfo("America/New_York")

        base = now.astimezone(local_tz)

        if frequency == "monthly":
            year = base.year
            month = base.month + 1
            if month > 12:
                month = 1
                year += 1
            max_day = monthrange(year, month)[1]
            clamped_day = min(day_of_month, max_day)
            next_run_local = base.replace(
                year=year,
                month=month,
                day=clamped_day,
                hour=hour,
                minute=minute,
                second=0,
                microsecond=0,
            )
        else:
            # Weekly and other frequencies not yet implemented — advance 7 days
            logger.warning(f"[scheduler] frequency '{frequency}' not fully implemented, advancing 7 days")
            from datetime import timedelta
            next_run_local = base + timedelta(days=7)
            next_run_local = next_run_local.replace(hour=hour, minute=minute, second=0, microsecond=0)

        next_run_at_utc = next_run_local.astimezone(timezone.utc)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                update report_schedules
                set last_run_at = %s,
                    next_run_at = %s,
                    updated_at = now()
                where id = %s
                """,
                (now, next_run_at_utc, str(schedule_id)),
            )
        conn.commit()


def run_scheduled_reports() -> None:
    now = datetime.now(timezone.utc)
    schedules = _fetch_due_schedules(now)

    logger.info(f"[scheduler] now={now.isoformat()} due_schedules={len(schedules)}")

    for schedule in schedules:
        schedule_id = schedule["id"]
        business_id = schedule["business_id"]

        try:
            logger.info(f"[scheduler] processing schedule={schedule_id} business={business_id}")

            recipients = _fetch_schedule_recipients(UUID(str(business_id)))
            if not recipients:
                logger.info(f"[scheduler] no recipients for schedule={schedule_id}, skipping send")
                _mark_schedule_run(schedule, now)
                continue

            report = generate_business_report(UUID(str(business_id)))
            if hasattr(report, "model_dump"):
                report = report.model_dump()
            elif hasattr(report, "dict"):
                report = report.dict()

            if not isinstance(report, dict) or not report.get("id"):
                raise ValueError(f"generate_business_report returned unexpected result: {type(report)}")

            report_id = UUID(str(report["id"]))

            for recipient in recipients:
                to_email = str(recipient["email"]).strip()
                if not to_email:
                    continue
                try:
                    logger.info(f"[scheduler] sending report={report_id} to={to_email}")
                    send_generated_report_email(
                        report_id,
                        SendReportRequest(to_email=to_email),
                    )
                except Exception as email_err:
                    logger.error(f"[scheduler] email failed report={report_id} to={to_email}: {email_err}")

            _mark_schedule_run(schedule, now)
            logger.info(f"[scheduler] completed schedule={schedule_id}")

        except Exception as e:
            logger.error(
                f"[scheduler] FAILED schedule={schedule_id} business={business_id}: {e}",
                exc_info=True,
            )
            # Still advance the schedule so it doesn't retry immediately next run.
            # This prevents one broken business from blocking the entire queue forever.
            try:
                _mark_schedule_run(schedule, now)
            except Exception as mark_err:
                logger.error(f"[scheduler] could not mark schedule={schedule_id} after failure: {mark_err}")


if __name__ == "__main__":
    run_scheduled_reports()