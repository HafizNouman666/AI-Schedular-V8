"""
Weekly quantity tracking alert job — runs every Friday at 8:00 AM US Mountain Time.

Checks all cost codes across all active jobs for the past 7 days.
If any item has reached 75%+ of its planned SOV/BI quantity, sends an
alert email to all NOTIFY_* recipients.

Business rule (from client doc):
  "Prompt PM/general superintendent and field supervisor when item is
   completed within 75% of SOV and/or BI quantity."

Monitoring:
  - After a successful send, a confirmation is sent to NOTIFY_TEST_EMAIL.
  - If no quantity data is found (HCSS issue), a no-data alert is sent
    to NOTIFY_TEST_EMAIL.
"""
from __future__ import annotations

import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

# Ensure repo root is on path when run directly
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from notifications.individual_email_sender import (
    get_all_notify_recipients,
    send_individual_email,
    send_no_data_alert,
    send_test_confirmation,
)
from quantity_tracking.tracker import QuantityResult, track_quantities

_MT = ZoneInfo("America/Denver")
logger = logging.getLogger(__name__)

_JOB_NAME = "Weekly Quantity Alert"


# ---------------------------------------------------------------------------
# Email builder
# ---------------------------------------------------------------------------

def _build_quantity_alert_email(
    alerts: list[QuantityResult],
    period_start: str,
    period_end: str,
) -> tuple[str, str]:
    """Build subject + HTML body for the weekly quantity alert email."""
    import html as html_mod

    now_str = datetime.now(_MT).strftime("%Y-%m-%d %H:%M %Z")
    count = len(alerts)

    subject = (
        f"Quantity Alert — {count} item(s) at or above 75% completion "
        f"| Week ending {period_end}"
    )

    rows_html = ""
    for r in alerts:
        status_colour = "#b91c1c" if r.status == "OVER_RISK" else "#c2410c"
        rows_html += (
            "<tr>"
            f'<td style="padding:8px 10px;border:1px solid #e5e7eb;font-family:Arial,Helvetica,sans-serif;font-size:13px;">'
            f"{html_mod.escape(r.cost_code)}</td>"
            f'<td style="padding:8px 10px;border:1px solid #e5e7eb;font-family:Arial,Helvetica,sans-serif;font-size:13px;">'
            f"{html_mod.escape(r.description)}</td>"
            f'<td style="padding:8px 10px;border:1px solid #e5e7eb;font-family:Arial,Helvetica,sans-serif;font-size:13px;">'
            f"{html_mod.escape(r.job_code)}</td>"
            f'<td style="padding:8px 10px;border:1px solid #e5e7eb;font-family:Arial,Helvetica,sans-serif;font-size:13px;">'
            f"{html_mod.escape(r.cost_type.replace('_', ' ').title())}</td>"
            f'<td style="padding:8px 10px;border:1px solid #e5e7eb;font-family:Arial,Helvetica,sans-serif;font-size:13px;">'
            f"{r.planned_quantity:,.2f} {html_mod.escape(r.unit)}</td>"
            f'<td style="padding:8px 10px;border:1px solid #e5e7eb;font-family:Arial,Helvetica,sans-serif;font-size:13px;">'
            f"{r.installed_quantity:,.2f} {html_mod.escape(r.unit)}</td>"
            f'<td style="padding:8px 10px;border:1px solid #e5e7eb;font-family:Arial,Helvetica,sans-serif;font-size:13px;'
            f'font-weight:bold;color:{status_colour};">'
            f"{r.percent_complete:.1f}%</td>"
            "</tr>"
        )

    html_body = (
        '<div style="font-family:Arial,Helvetica,sans-serif;color:#111827;max-width:800px;">'
        '<h2 style="margin:0 0 8px 0;font-size:18px;color:#111827;">Quantity Tracking Alert</h2>'
        f'<p style="margin:0 0 16px 0;font-size:14px;color:#374151;">'
        f"Tracking period: <strong>{html_mod.escape(period_start)}</strong> to "
        f"<strong>{html_mod.escape(period_end)}</strong></p>"
        f'<p style="margin:0 0 16px 0;font-size:14px;color:#b91c1c;font-weight:bold;">'
        f"{count} cost code(s) have reached 75% or more of their planned quantity. "
        f"Immediate review required.</p>"
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        'style="width:100%;border-collapse:collapse;margin-bottom:16px;">'
        '<thead><tr style="background:#f3f4f6;">'
        '<th style="padding:8px 10px;border:1px solid #e5e7eb;text-align:left;font-family:Arial,Helvetica,sans-serif;font-size:12px;color:#374151;">Cost Code</th>'
        '<th style="padding:8px 10px;border:1px solid #e5e7eb;text-align:left;font-family:Arial,Helvetica,sans-serif;font-size:12px;color:#374151;">Description</th>'
        '<th style="padding:8px 10px;border:1px solid #e5e7eb;text-align:left;font-family:Arial,Helvetica,sans-serif;font-size:12px;color:#374151;">Job</th>'
        '<th style="padding:8px 10px;border:1px solid #e5e7eb;text-align:left;font-family:Arial,Helvetica,sans-serif;font-size:12px;color:#374151;">Type</th>'
        '<th style="padding:8px 10px;border:1px solid #e5e7eb;text-align:left;font-family:Arial,Helvetica,sans-serif;font-size:12px;color:#374151;">Planned Qty</th>'
        '<th style="padding:8px 10px;border:1px solid #e5e7eb;text-align:left;font-family:Arial,Helvetica,sans-serif;font-size:12px;color:#374151;">Installed Qty</th>'
        '<th style="padding:8px 10px;border:1px solid #e5e7eb;text-align:left;font-family:Arial,Helvetica,sans-serif;font-size:12px;color:#374151;">% Complete</th>'
        "</tr></thead>"
        f"<tbody>{rows_html}</tbody>"
        "</table>"
        '<p style="margin:24px 0 0 0;padding-top:16px;border-top:1px solid #e5e7eb;'
        'font-size:12px;color:#6b7280;font-family:Arial,Helvetica,sans-serif;">'
        f"Sent by Gould APM — Quantity Tracking System — {html_mod.escape(now_str)}"
        "</p>"
        "</div>"
    )

    return subject, html_body


# ---------------------------------------------------------------------------
# Main job function  (called by APScheduler + background_scheduler.py)
# ---------------------------------------------------------------------------

def run_weekly_quantity_alerts() -> None:
    """
    1. Track quantities for the past 7 days (calls HCSS directly — weekly cadence).
    2. Filter items >= 75% complete.
    3. If no results at all (HCSS failed) → send no-data alert to test address.
    4. If no alerts (all items under 75%) → log info, send test confirmation.
    5. If alerts exist → send alert email to team + confirmation to test address.
    """
    today = date.today()
    start_date = (today - timedelta(days=7)).isoformat()
    end_date = today.isoformat()

    logger.info(
        "Weekly quantity alert job started: period=%s to %s",
        start_date,
        end_date,
    )

    # ── Step 1: Fetch quantity data ───────────────────────────────────────
    try:
        results = track_quantities(start_date=start_date, end_date=end_date)
    except Exception as exc:
        reason = f"HCSS quantity tracking failed: {exc}"
        logger.error("Weekly quantity job aborted: %s", reason)
        send_no_data_alert(
            job_name=_JOB_NAME,
            report_date=f"{start_date} to {end_date}",
            reason=reason,
        )
        return

    # ── Step 2: Filter alerts ─────────────────────────────────────────────
    alerts = [r for r in results if r.alert]

    logger.info(
        "Weekly quantity job: total_cost_codes=%d alerts=%d",
        len(results),
        len(alerts),
    )

    # ── Step 3: Handle zero results (HCSS returned nothing) ───────────────
    if not results:
        reason = (
            f"No quantity data returned from HCSS for period {start_date} to {end_date}. "
            "There may be no active jobs this week, or HCSS API may be unavailable."
        )
        logger.warning("Weekly quantity job: no results from HCSS — %s", reason)
        send_no_data_alert(
            job_name=_JOB_NAME,
            report_date=f"{start_date} to {end_date}",
            reason=reason,
        )
        return

    # ── Step 4: Handle no alerts (all items under 75%) ────────────────────
    if not alerts:
        logger.info(
            "No quantity alerts this week — all %d cost codes under 75%%. "
            "No team email sent.",
            len(results),
        )
        # Still send a test confirmation so you know the job ran
        send_test_confirmation(
            job_name=_JOB_NAME,
            report_date=f"{start_date} to {end_date}",
            recipients_count=0,
            extra_detail=(
                f"All {len(results)} cost code(s) are under 75% completion. "
                "No alert email was sent to the team."
            ),
        )
        return

    # ── Step 5: Get recipients ────────────────────────────────────────────
    recipients = get_all_notify_recipients()
    if not recipients:
        logger.error(
            "No recipients configured — set NOTIFY_SUPERVISOR_EMAIL, "
            "NOTIFY_PM_EMAIL, or NOTIFY_ADMIN_EMAIL in .env"
        )
        send_no_data_alert(
            job_name=_JOB_NAME,
            report_date=f"{start_date} to {end_date}",
            reason=(
                f"{len(alerts)} alert(s) found but no recipients are configured. "
                "Set NOTIFY_* env variables."
            ),
        )
        return

    # ── Step 6: Build and send alert email ────────────────────────────────
    subject, html_body = _build_quantity_alert_email(alerts, start_date, end_date)

    try:
        send_individual_email(
            subject=subject,
            html_body=html_body,
            recipients=recipients,
        )
        logger.info(
            "Weekly quantity alert sent to %d recipient(s): %s",
            len(recipients),
            ", ".join(recipients),
        )
    except Exception as exc:
        logger.exception("Failed to send weekly quantity alert: %s", exc)
        send_no_data_alert(
            job_name=_JOB_NAME,
            report_date=f"{start_date} to {end_date}",
            reason=f"Email send failed (SMTP error): {exc}",
        )
        return

    # ── Step 7: Send test confirmation ────────────────────────────────────
    over_risk = sum(1 for r in alerts if r.status == "OVER_RISK")
    near_completion = sum(1 for r in alerts if r.status == "NEAR_COMPLETION")

    send_test_confirmation(
        job_name=_JOB_NAME,
        report_date=f"{start_date} to {end_date}",
        recipients_count=len(recipients),
        extra_detail=(
            f"Alerts — Over Risk: {over_risk} | Near Completion: {near_completion} "
            f"| Total Alerted: {len(alerts)} of {len(results)} cost codes"
        ),
    )


# ---------------------------------------------------------------------------
# Standalone scheduler — only used when running weekly_quantity_job.py directly.
# When running via uvicorn, background_scheduler.py adds this job instead.
# ---------------------------------------------------------------------------

def create_quantity_scheduler() -> BlockingScheduler:
    scheduler = BlockingScheduler(timezone=_MT)
    scheduler.add_job(
        run_weekly_quantity_alerts,
        CronTrigger(
            day_of_week="fri",
            hour=8,
            minute=0,
            second=0,
            timezone=_MT,
        ),
        id="weekly_quantity_alerts",
        replace_existing=True,
    )
    logger.info("Standalone scheduler created — weekly job fires every Friday at 08:00 MT")
    return scheduler


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    logger.info("Starting Gould APM Weekly Quantity Scheduler (standalone)")
    create_quantity_scheduler().start()


if __name__ == "__main__":
    main()