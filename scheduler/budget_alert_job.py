"""
Budget Alert Scheduler Jobs — scheduler/budget_alert_job.py

Two budget monitoring jobs that run automatically inside the FastAPI process:

  Job 1 — run_daily_budget_alert()
    Fires:   Every day at 2:00 PM US Mountain Time
    Purpose: Reads yesterday's budget from DB cache, finds OVER_RISK items
             (85%+ utilization), sends alert email to team if any found.
             If nothing at risk → silent skip (test confirmation only).
    Pattern: Mirrors daily_job.py exactly — reads from DB, never calls HCSS.

  Job 2 — run_weekly_budget_summary()
    Fires:   Every Monday at 8:00 AM US Mountain Time
    Purpose: Aggregates last 7 days of budget data from DB, sends full
             summary (ON_TRACK + OVER_RISK) to team every week.
    Pattern: Mirrors weekly_quantity_job.py — aggregates across dates, always sends.

Business rules implemented (from client APM workflow doc):
  - "Track cost vs budget daily"
  - "Report to project team weekly"
  - "15% or more loss → report daily" (mapped to 85% utilization = OVER_RISK)
  - "75% of budget used → alert" (mapped to OVER_RISK threshold in analyzer.py)

No HCSS calls here — data is already in DB from unified_cron_job.py (runs every 8h).
Email uses existing notifications/budget_email_sender.py + budget_email_template.py.
"""
from __future__ import annotations

import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

# ── Ensure repo root is on path when run directly ────────────────────────────
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from notifications.budget_email_sender import send_budget_email
from notifications.budget_email_template import build_budget_email_body
from notifications.individual_email_sender import (
    get_all_notify_recipients,
    send_no_data_alert,
    send_test_confirmation,
)

logger = logging.getLogger(__name__)

_MT = ZoneInfo("America/Denver")

_DAILY_JOB_NAME  = "Daily Budget Alert"
_WEEKLY_JOB_NAME = "Weekly Budget Summary"


# ============================================================================
# INTERNAL HELPERS
# ============================================================================

def _report_date() -> str:
    """Yesterday's date in US Mountain Time as YYYY-MM-DD."""
    return (datetime.now(_MT).date() - timedelta(days=1)).isoformat()


def _date_list(start_date: str, end_date: str) -> list[str]:
    """Return an inclusive list of YYYY-MM-DD strings from start to end."""
    dates: list[str] = []
    cur = datetime.fromisoformat(start_date)
    end = datetime.fromisoformat(end_date)
    while cur <= end:
        dates.append(cur.strftime("%Y-%m-%d"))
        cur += timedelta(days=1)
    return dates


def _load_budget_from_db(target_date: str) -> list[dict] | None:
    """
    Read budget tracking results from DB cache for a single date.

    Returns:
        list[dict]  — budget items for that date (may be empty list)
        None        — cache miss (unified cron hasn't run yet for this date)
    """
    from api.database import SessionLocal
    from api.db_services import get_budget_tracking

    db = SessionLocal()
    try:
        summary, results = get_budget_tracking(db, target_date)
        if summary is None:
            logger.warning(
                "Budget DB cache miss for date=%s — unified cron may not have run",
                target_date,
            )
            return None
        logger.info(
            "Loaded %d budget item(s) from DB cache for date=%s",
            len(results), target_date,
        )
        return results
    except Exception as exc:
        logger.exception(
            "Failed to read budget DB cache for date=%s: %s", target_date, exc
        )
        return None
    finally:
        db.close()


def _load_budget_range_from_db(start_date: str, end_date: str) -> list[dict]:
    """
    Read raw budget dicts from DB across a date range.
    Uses get_or_fetch_budget_for_date so it fetches from HCSS on a DB miss.
    Returns a flat list of all daily rows (un-aggregated).
    """
    from api.database import SessionLocal
    from api.db_services import get_or_fetch_budget_for_date

    dates = _date_list(start_date, end_date)
    raw: list[dict] = []
    db = SessionLocal()
    try:
        for d in dates:
            daily = get_or_fetch_budget_for_date(db, d)
            raw.extend(daily)
            logger.debug("Budget range load: date=%s rows=%d", d, len(daily))
    except Exception as exc:
        logger.exception("Failed to load budget range %s→%s: %s", start_date, end_date, exc)
    finally:
        db.close()

    logger.info(
        "Budget range load complete: %s→%s total_rows=%d",
        start_date, end_date, len(raw),
    )
    return raw


def _aggregate_budget(raw: list[dict]) -> list[dict]:
    """
    Aggregate raw budget dicts by (job_id, cost_code_id), summing actual
    costs across dates, then recalculate utilization / variance / status.

    Mirrors _aggregate_budget() in api/routes/budget.py exactly so results
    are consistent with the API and frontend.
    """
    agg: dict[tuple, dict] = {}

    for item in raw:
        if not item.get("cost_code", "").strip():
            continue
        key = (item["job_id"], item["cost_code_id"])
        if key in agg:
            agg[key]["actual_cost"]      += item.get("actual_cost", 0)
            agg[key]["labor_cost"]       += item.get("labor_cost", 0)
            agg[key]["equipment_cost"]   += item.get("equipment_cost", 0)
            agg[key]["material_cost"]    += item.get("material_cost", 0)
            agg[key]["subcontract_cost"] += item.get("subcontract_cost", 0)
            agg[key]["trucking_cost"]    += item.get("trucking_cost", 0)
            agg[key]["labor_hours"]      += item.get("labor_hours", 0)
            agg[key]["quantity"]         += item.get("quantity", 0)
        else:
            agg[key] = dict(item)
            agg[key].setdefault("labor_cost", 0.0)
            agg[key].setdefault("equipment_cost", 0.0)
            agg[key].setdefault("material_cost", 0.0)
            agg[key].setdefault("subcontract_cost", 0.0)
            agg[key].setdefault("trucking_cost", 0.0)
            agg[key].setdefault("labor_hours", 0.0)
            agg[key].setdefault("quantity", 0.0)

    result: list[dict] = []
    for item in agg.values():
        expected    = item.get("expected_budget", 0.0)
        actual      = item.get("actual_cost", 0.0)
        utilization = (
            round((actual / expected) * 100)
            if expected > 0
            else (0 if actual == 0 else 999)
        )
        item["utilization_percentage"] = utilization
        item["variance"] = actual - expected

        if expected <= 0:
            item["status"] = "OVER_RISK" if actual > 0 else "ON_TRACK"
        elif utilization < 85:
            item["status"] = "ON_TRACK"
        else:
            item["status"] = "OVER_RISK"

        result.append(item)

    return result


def _to_email_item(d: dict) -> SimpleNamespace:
    """
    Convert a budget dict to a SimpleNamespace so build_budget_email_body()
    can access fields as attributes (item.status, item.job_name, etc).
    Avoids importing BudgetItemRow from api/routes/budget.py.
    """
    return SimpleNamespace(
        cost_code_id          = d.get("cost_code_id", ""),
        cost_code             = d.get("cost_code", ""),
        cost_code_description = d.get("cost_code_description", ""),
        job_id                = d.get("job_id", ""),
        job_name              = d.get("job_name", ""),
        business_unit         = d.get("business_unit") or "N/A",
        expected_budget       = d.get("expected_budget", 0.0),
        actual_cost           = d.get("actual_cost", 0.0),
        utilization_percentage= d.get("utilization_percentage", 0),
        variance              = d.get("variance", 0.0),
        status                = d.get("status", "ON_TRACK"),
        labor_cost            = d.get("labor_cost", 0.0),
        equipment_cost        = d.get("equipment_cost", 0.0),
        material_cost         = d.get("material_cost", 0.0),
        subcontract_cost      = d.get("subcontract_cost", 0.0),
        trucking_cost         = d.get("trucking_cost", 0.0),
        labor_hours           = d.get("labor_hours", 0.0),
        quantity              = d.get("quantity", 0.0),
        labor_budget          = d.get("labor_budget", 0.0),
        equipment_budget      = d.get("equipment_budget", 0.0),
        material_budget       = d.get("material_budget", 0.0),
        subcontract_budget    = d.get("subcontract_budget", 0.0),
        foremen               = d.get("foremen", []),
    )


# ============================================================================
# JOB 1 — DAILY BUDGET ALERT (2:00 PM MT)
# ============================================================================

def run_daily_budget_alert() -> None:
    """
    Reads yesterday's budget from DB cache, sends an alert email to the team
    if any cost codes are at OVER_RISK (85%+ utilization).

    Flow:
        1. Determine yesterday's date (Mountain Time).
        2. Load from DB cache — abort with no-data alert if cache miss.
        3. Filter OVER_RISK items only.
        4. If nothing at risk → log it + send test confirmation, no team email.
        5. Get NOTIFY_* recipients from .env.
        6. Build email using existing budget template.
        7. Send to team via existing budget email sender.
        8. Send test confirmation to NOTIFY_TEST_EMAIL.
    """
    target_date = _report_date()
    logger.info("[%s] Job started for date=%s", _DAILY_JOB_NAME, target_date)

    # ── Step 1: Load from DB ─────────────────────────────────────────────
    results = _load_budget_from_db(target_date)

    if results is None:
        reason = (
            f"No budget data in database for {target_date}. "
            "The unified sync job (runs every 8h) may have failed or not run yet. "
            "Check server logs for errors from scheduler/unified_cron_job.py."
        )
        logger.error("[%s] Aborted — %s", _DAILY_JOB_NAME, reason)
        send_no_data_alert(
            job_name=_DAILY_JOB_NAME,
            report_date=target_date,
            reason=reason,
        )
        return

    if len(results) == 0:
        reason = (
            f"DB cache exists for {target_date} but contains 0 budget items. "
            "No active jobs with budget data on this date. "
            "This may be normal for weekends or holidays."
        )
        logger.info("[%s] Zero items for %s — skipping", _DAILY_JOB_NAME, target_date)
        send_no_data_alert(
            job_name=_DAILY_JOB_NAME,
            report_date=target_date,
            reason=reason,
        )
        return

    # ── Step 2: Filter OVER_RISK only ───────────────────────────────────
    over_risk = [r for r in results if r.get("status") == "OVER_RISK"]
    on_track  = [r for r in results if r.get("status") == "ON_TRACK"]

    logger.info(
        "[%s] date=%s total=%d over_risk=%d on_track=%d",
        _DAILY_JOB_NAME, target_date, len(results), len(over_risk), len(on_track),
    )

    # ── Step 3: Skip if nothing at risk ─────────────────────────────────
    if not over_risk:
        logger.info(
            "[%s] All %d cost codes ON_TRACK for %s — no team email sent",
            _DAILY_JOB_NAME, len(results), target_date,
        )
        send_test_confirmation(
            job_name=_DAILY_JOB_NAME,
            report_date=target_date,
            recipients_count=0,
            extra_detail=(
                f"All {len(results)} cost code(s) are ON_TRACK (under 85% utilization). "
                "No alert email was sent to the team."
            ),
        )
        return

    # ── Step 4: Get recipients ───────────────────────────────────────────
    recipients = get_all_notify_recipients()
    if not recipients:
        logger.error("[%s] No recipients configured", _DAILY_JOB_NAME)
        send_no_data_alert(
            job_name=_DAILY_JOB_NAME,
            report_date=target_date,
            reason=(
                f"{len(over_risk)} OVER_RISK item(s) found but no recipients configured. "
                "Set NOTIFY_SUPERVISOR_EMAIL / NOTIFY_PM_EMAIL / NOTIFY_ADMIN_EMAIL in .env."
            ),
        )
        return

    # ── Step 5: Build email ──────────────────────────────────────────────
    email_items = [_to_email_item(r) for r in over_risk]
    subject, html_body = build_budget_email_body(
        period_start=target_date,
        period_end=target_date,
        items=email_items,
        comments=(
            f"DAILY ALERT — {len(over_risk)} cost code(s) have reached 85% or more "
            "of their budget. Immediate review required."
        ),
    )
    # Override subject for clear alert identification
    subject = (
        f"Budget Alert — {len(over_risk)} OVER RISK item(s) require review | {target_date}"
    )

    # ── Step 6: Send to team ─────────────────────────────────────────────
    try:
        send_budget_email(
            subject=subject,
            html_body=html_body,
            recipients=recipients,
        )
        logger.info(
            "[%s] Alert sent: %d over-risk items to %d recipient(s)",
            _DAILY_JOB_NAME, len(over_risk), len(recipients),
        )
    except Exception as exc:
        logger.exception(
            "[%s] Failed to send email for date=%s: %s",
            _DAILY_JOB_NAME, target_date, exc,
        )
        send_no_data_alert(
            job_name=_DAILY_JOB_NAME,
            report_date=target_date,
            reason=f"Email send failed (SMTP error): {exc}",
        )
        return

    # ── Step 7: Test confirmation ────────────────────────────────────────
    send_test_confirmation(
        job_name=_DAILY_JOB_NAME,
        report_date=target_date,
        recipients_count=len(recipients),
        extra_detail=(
            f"Over Risk: {len(over_risk)} | On Track: {len(on_track)} "
            f"| Total Cost Codes: {len(results)}"
        ),
    )


# ============================================================================
# JOB 2 — WEEKLY BUDGET SUMMARY (Monday 8:00 AM MT)
# ============================================================================

def run_weekly_budget_summary() -> None:
    """
    Aggregates budget data across the past 7 days from DB, sends a full
    summary email to the team every Monday (ON_TRACK + OVER_RISK both included).

    Flow:
        1. Compute date range: last 7 days.
        2. Load and aggregate from DB across all 7 dates.
        3. Abort with no-data alert if nothing in DB.
        4. Get NOTIFY_* recipients.
        5. Build full summary email using existing budget template.
        6. Send to team.
        7. Send test confirmation.
    """
    today      = date.today()
    end_date   = (today - timedelta(days=1)).isoformat()   # yesterday
    start_date = (today - timedelta(days=7)).isoformat()   # 7 days back

    logger.info(
        "[%s] Job started: period=%s to %s",
        _WEEKLY_JOB_NAME, start_date, end_date,
    )

    # ── Step 1: Load and aggregate from DB ──────────────────────────────
    raw = _load_budget_range_from_db(start_date, end_date)

    if not raw:
        reason = (
            f"No budget data found in database for period {start_date} to {end_date}. "
            "The unified sync job may have failed for all dates in this window. "
            "Check server logs for errors from scheduler/unified_cron_job.py."
        )
        logger.error("[%s] Aborted — %s", _WEEKLY_JOB_NAME, reason)
        send_no_data_alert(
            job_name=_WEEKLY_JOB_NAME,
            report_date=f"{start_date} to {end_date}",
            reason=reason,
        )
        return

    aggregated      = _aggregate_budget(raw)
    over_risk_count = sum(1 for r in aggregated if r.get("status") == "OVER_RISK")
    on_track_count  = sum(1 for r in aggregated if r.get("status") == "ON_TRACK")

    logger.info(
        "[%s] Aggregated: total=%d over_risk=%d on_track=%d",
        _WEEKLY_JOB_NAME, len(aggregated), over_risk_count, on_track_count,
    )

    # ── Step 2: Get recipients ───────────────────────────────────────────
    recipients = get_all_notify_recipients()
    if not recipients:
        logger.error("[%s] No recipients configured", _WEEKLY_JOB_NAME)
        send_no_data_alert(
            job_name=_WEEKLY_JOB_NAME,
            report_date=f"{start_date} to {end_date}",
            reason=(
                "Weekly budget summary ready but no recipients configured. "
                "Set NOTIFY_SUPERVISOR_EMAIL / NOTIFY_PM_EMAIL / NOTIFY_ADMIN_EMAIL in .env."
            ),
        )
        return

    # ── Step 3: Build email ──────────────────────────────────────────────
    email_items = [_to_email_item(r) for r in aggregated]
    subject, html_body = build_budget_email_body(
        period_start=start_date,
        period_end=end_date,
        items=email_items,
        comments="",
    )
    subject = (
        f"Weekly Budget Summary — {over_risk_count} Over Risk, "
        f"{on_track_count} On Track | {start_date} to {end_date}"
    )

    # ── Step 4: Send to team ─────────────────────────────────────────────
    try:
        send_budget_email(
            subject=subject,
            html_body=html_body,
            recipients=recipients,
        )
        logger.info(
            "[%s] Summary sent: %d items to %d recipient(s)",
            _WEEKLY_JOB_NAME, len(aggregated), len(recipients),
        )
    except Exception as exc:
        logger.exception("[%s] Failed to send email: %s", _WEEKLY_JOB_NAME, exc)
        send_no_data_alert(
            job_name=_WEEKLY_JOB_NAME,
            report_date=f"{start_date} to {end_date}",
            reason=f"Email send failed (SMTP error): {exc}",
        )
        return

    # ── Step 5: Test confirmation ────────────────────────────────────────
    send_test_confirmation(
        job_name=_WEEKLY_JOB_NAME,
        report_date=f"{start_date} to {end_date}",
        recipients_count=len(recipients),
        extra_detail=(
            f"Over Risk: {over_risk_count} | On Track: {on_track_count} "
            f"| Total Cost Codes: {len(aggregated)}"
        ),
    )