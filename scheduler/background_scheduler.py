from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from scheduler.unified_cron_job import run_unified_cron_job
from scheduler.monthly_projection_job import run_monthly_projection_job   # ← NEW

from scheduler.daily_job import run_daily_time_log_report
from scheduler.weekly_quantity_job import run_weekly_quantity_alerts

from scheduler.budget_alert_job import run_daily_budget_alert, run_weekly_budget_summary

logger = logging.getLogger(__name__)

_MT = ZoneInfo("America/Denver")

scheduler: BackgroundScheduler | None = None

# ─────────────────────────────────────────────
# EMAIL TOGGLE (NEW)
# ─────────────────────────────────────────────
EMAILS_ENABLED = os.getenv("EMAILS_ENABLED", "true").lower() == "true"


def run_unified_sync_job() -> None:
    """
    Entry point called by APScheduler every 8 hours.
    Handles: timelog, quantity, budget (daily modules).
    Projection is handled separately by the monthly job below.
    """
    run_unified_cron_job()


def _run_daily_report() -> None:
    """Read DB cache, build report, email the team. Runs at 1 PM MT daily."""
    run_daily_time_log_report()


def _run_weekly_quantity() -> None:
    """Fetch quantity data, check 75% threshold, email alerts. Runs Friday 8 AM MT."""
    run_weekly_quantity_alerts()


def _run_daily_budget_alert() -> None:
    run_daily_budget_alert()


def _run_weekly_budget_summary() -> None:
    run_weekly_budget_summary()


def start_background_scheduler() -> None:
    """
    Start the background scheduler when FastAPI starts.
    Called from the FastAPI lifespan startup event.

    Jobs registered:
    1. unified_sync_job        — every 8 hours  (timelog, quantity, budget)
    2. daily_report_job        — daily at 1 PM Mountain Time
    3. weekly_quantity_job     — Friday at 8 AM Mountain Time
    4. monthly_projection_job  — 23rd each month (projections)
    5. daily_budget_alert_job  — daily at 2 PM Mountain Time
    6. weekly_budget_summary   — Monday at 8 AM Mountain Time
    7. startup_sync            — 2 min after start (one-shot)
    """
    global scheduler

    if scheduler is not None:
        logger.warning("Scheduler already running")
        return

    logger.info("Starting background scheduler...")

    scheduler = BackgroundScheduler(
        timezone="UTC",
        job_defaults={
            "coalesce": True,
            "max_instances": 1,
            "misfire_grace_time": 3600,
        },
    )

    # ── Job 1: 8-hour sync (timelog, quantity, budget) ───────────────────
    scheduler.add_job(
        func=run_unified_sync_job,
        trigger=CronTrigger(hour="0,8,16", minute=0),
        id="unified_sync_job",
        name="Unified Data Sync Job (Timelog / Quantity / Budget)",
        replace_existing=True,
    )

    # ── EMAIL JOBS (CONTROLLED BY ENV) ───────────────────────────────────
    if EMAILS_ENABLED:

        # ── Job 2: Daily time log report — 1:00 PM Mountain Time ─────────
        scheduler.add_job(
            func=_run_daily_report,
            trigger=CronTrigger(hour=13, minute=0, second=0, timezone=_MT),
            id="daily_report_job",
            name="Daily Time Log Report Email",
            replace_existing=True,
        )

        # ── Job 3: Weekly quantity alert — Friday 8:00 AM Mountain Time ──
        scheduler.add_job(
            func=_run_weekly_quantity,
            trigger=CronTrigger(
                day_of_week="fri",
                hour=8,
                minute=0,
                second=0,
                timezone=_MT,
            ),
            id="weekly_quantity_job",
            name="Weekly Quantity Alert Email",
            replace_existing=True,
        )

        # ── Budget Job 4A: Daily alert — 2:00 PM Mountain Time ────────────
        scheduler.add_job(
            func=_run_daily_budget_alert,
            trigger=CronTrigger(hour=14, minute=0, second=0, timezone=_MT),
            id="daily_budget_alert_job",
            name="Daily Budget Alert Email",
            replace_existing=True,
        )

        # ── Budget Job 4B: Weekly summary — Monday 8:00 AM Mountain Time ──
        scheduler.add_job(
            func=_run_weekly_budget_summary,
            trigger=CronTrigger(
                day_of_week="mon",
                hour=8,
                minute=0,
                second=0,
                timezone=_MT,
            ),
            id="weekly_budget_summary_job",
            name="Weekly Budget Summary Email",
            replace_existing=True,
        )

        logger.info("Email jobs ENABLED via EMAILS_ENABLED=true")

    else:
        logger.info("Email jobs DISABLED via EMAILS_ENABLED=false")

    # ── Job 5: Monthly projection — 23rd at 08:00 UTC ────────────────────
    scheduler.add_job(
        func=run_monthly_projection_job,
        trigger=CronTrigger(day=23, hour=8, minute=0),
        id="monthly_projection_job",
        name="Monthly Projection Calculation",
        replace_existing=True,
    )

    # ── Job 6: One-shot startup sync (2 min after boot) ───────────────────
    scheduler.add_job(
        func=run_unified_sync_job,
        trigger="date",
        run_date=datetime.now() + timedelta(minutes=2),
        id="startup_sync",
        name="Startup Data Sync",
    )

    scheduler.start()

    logger.info("✓ Background scheduler started successfully")
    logger.info("  - Sync job (timelog/quantity/budget) runs every 8 hours (00:00, 08:00, 16:00 UTC)")
    logger.info("  - Monthly projection runs on 23rd of each month at 08:00 UTC")
    logger.info("  - Initial sync will run in 2 minutes")

    unified_job = scheduler.get_job("unified_sync_job")
    if unified_job and unified_job.next_run_time:
        logger.info("  - Next unified sync: %s", unified_job.next_run_time)

    projection_job = scheduler.get_job("monthly_projection_job")
    if projection_job and projection_job.next_run_time:
        logger.info("  - Next projection run: %s", projection_job.next_run_time)


def stop_background_scheduler() -> None:
    """Stop the background scheduler when FastAPI shuts down."""
    global scheduler
    if scheduler is None:
        return
    logger.info("Stopping background scheduler...")
    scheduler.shutdown(wait=True)
    scheduler = None
    logger.info("✓ Background scheduler stopped")


def get_scheduler_status() -> dict:
    """Returns current scheduler status and all job info."""
    if scheduler is None:
        return {"running": False, "jobs": []}

    jobs = [
        {
            "id": job.id,
            "name": job.name,
            "next_run_time": job.next_run_time.isoformat() if job.next_run_time else None,
            "trigger": str(job.trigger),
        }
        for job in scheduler.get_jobs()
    ]
    return {"running": scheduler.running, "jobs": jobs}