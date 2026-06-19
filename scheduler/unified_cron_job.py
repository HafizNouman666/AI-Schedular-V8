"""
Unified cron job for all tracking modules (timelog, quantity, budget).
Runs every 8 hours and ensures all data is up-to-date with retry logic.
"""
from __future__ import annotations

import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy.orm import Session
from sqlalchemy import and_

from api.database import SessionLocal, create_tables, CronJobExecution
from api.db_services import (
    store_timelog_verification,
    store_quantity_tracking,
    store_budget_tracking,
    store_projection_tracking,
    create_cron_job_execution,
    update_cron_job_status,
    get_pending_retry_jobs,
    schedule_retry,
    check_if_date_processed,
)
from payroll_verification.verifier import verify_payroll_date
from quantity_tracking.tracker import track_quantities
from budget_tracking.hcss_client import BudgetHCSSClient
from budget_tracking.analyzer import calculate_budget_status

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ===========================================================================
# CONFIGURATION
# ===========================================================================

# How many days back to check for missing data
LOOKBACK_DAYS = 7

# Maximum retries for failed jobs
MAX_RETRIES = 3

# Retry delay in minutes
RETRY_DELAY_MINUTES = 60


# ===========================================================================
# INDIVIDUAL MODULE PROCESSORS
# ===========================================================================

def process_timelog_for_date(db: Session, target_date: str) -> tuple[bool, int, str | None]:
    """
    Process time log verification for a specific date.
    
    Returns:
        Tuple of (success, records_processed, error_message)
    """
    try:
        logger.info("Processing timelog for date: %s", target_date)
        
        # Fetch and verify timecards
        results = verify_payroll_date(target_date=target_date, business_unit_id=None)
        
        # Convert results to dict format
        results_dict = [
            {
                "id": r.id,
                "date": r.date,
                "job": r.job_code,  # Fixed: was r.job, should be r.job_code
                "job_id": r.job_id,
                "foreman": r.foreman,
                "foreman_id": r.foreman_id,
                "status": r.status,
                "reasons": r.reasons,
                "flags": r.flags,
                "why": r.why,
            }
            for r in results
        ]
        
        # Store in database
        cache_id, records_stored = store_timelog_verification(
            db=db,
            verification_date=target_date,
            business_unit_id=None,
            results=results_dict,
        )
        
        logger.info("✓ Timelog processed: date=%s records=%d", target_date, records_stored)
        return True, records_stored, None
        
    except Exception as exc:
        error_msg = f"Timelog processing failed: {exc}"
        logger.error(error_msg)
        return False, 0, error_msg


def process_quantity_for_date(db: Session, target_date: str) -> tuple[bool, int, str | None]:
    """
    Process quantity tracking for a specific date.
    
    Returns:
        Tuple of (success, records_processed, error_message)
    """
    try:
        logger.info("Processing quantity tracking for date: %s", target_date)
        
        # Track quantities
        results = track_quantities(
            start_date=target_date,
            end_date=target_date,
        )
        
        # Convert results to dict format
        results_dict = [
            {
                "cost_code_id": r.cost_code_id,
                "cost_code": r.cost_code,
                "description": r.description,
                "job_id": r.job_id,
                "job_code": r.job_code,
                "unit": r.unit,
                "cost_type": r.cost_type,
                "planned_quantity": r.planned_quantity,
                "installed_quantity": r.installed_quantity,
                "remaining_quantity": r.remaining_quantity,
                "percent_complete": r.percent_complete,
                "status": r.status,
                "alert": r.alert,
            }
            for r in results
        ]
        
        # Store in database
        cache_id, records_stored = store_quantity_tracking(
            db=db,
            tracking_date=target_date,
            results=results_dict,
        )
        
        logger.info("✓ Quantity tracking processed: date=%s records=%d", target_date, records_stored)
        return True, records_stored, None
        
    except Exception as exc:
        error_msg = f"Quantity tracking failed: {exc}"
        logger.error(error_msg)
        return False, 0, error_msg


def process_budget_for_date(db: Session, target_date: str) -> tuple[bool, int, str | None]:
    """
    Process budget tracking for a specific date.

    Stores budgeted_all_cost (raw HCSS total) in DB.
    expected_budget is NOT stored — it is computed on the fly at response time
    using: (quantity / planned_quantity) * budgeted_all_cost

    Returns:
        Tuple of (success, records_processed, error_message)
    """
    try:
        logger.info("Processing budget tracking for date: %s", target_date)

        # Fetch budget data from HCSS
        client = BudgetHCSSClient()
        raw_data = client.fetch_budget_summary(date=target_date)

        # Parse into BudgetResult objects (no metrics — just budgeted_all_cost + actual_cost)
        results = calculate_budget_status(raw_data)

        # Build lookup of extra fields from raw HCSS response by (jobId, costCodeId)
        raw_by_key: dict[tuple, dict] = {}
        for item in raw_data:
            jid  = (item.get("job")      or {}).get("jobId",      "")
            ccid = (item.get("costCode") or {}).get("costCodeId", "")
            if jid and ccid:
                raw_by_key[(jid, ccid)] = item

        results_dict = []
        for r in results:
            extra = raw_by_key.get((r.job_id, r.cost_code_id), {})
            results_dict.append({
                "cost_code_id":          r.cost_code_id,
                "cost_code":             r.cost_code,
                "cost_code_description": r.cost_code_description,
                "job_id":                r.job_id,
                "job_name":              r.job_name,
                "business_unit":         r.business_unit,
                # Raw HCSS total budget — stored in DB as budgeted_all_cost
                "budgeted_all_cost":     r.budgeted_all_cost,
                "actual_cost":           r.actual_cost,
                # Actual cost breakdown from raw HCSS response
                "labor_cost":       extra.get("laborCost",       0.0),
                "equipment_cost":   extra.get("equipmentCost",   0.0),
                "material_cost":    extra.get("materialCost",    0.0),
                "subcontract_cost": extra.get("subcontractCost", 0.0),
                "trucking_cost":    extra.get("truckingCost",    0.0),
                "labor_hours":      extra.get("laborHours",      0.0),
                "quantity":         extra.get("quantity",        0.0),
                # Planned budget breakdown from raw HCSS response
                "labor_budget":       extra.get("laborBudget",       0.0),
                "equipment_budget":   extra.get("equipmentBudget",   0.0),
                "material_budget":    extra.get("materialBudget",    0.0),
                "subcontract_budget": extra.get("subcontractBudget", 0.0),
            })

        # Store in database
        cache_id, records_stored = store_budget_tracking(
            db=db,
            tracking_date=target_date,
            results=results_dict,
        )

        logger.info("✓ Budget tracking processed: date=%s records=%d", target_date, records_stored)
        return True, records_stored, None

    except Exception as exc:
        error_msg = f"Budget tracking failed: {exc}"
        logger.error(error_msg)
        return False, 0, error_msg


# ===========================================================================
# UNIFIED PROCESSOR
# ===========================================================================

def process_single_module(
    db: Session,
    job_type: str,
    target_date: str,
) -> bool:
    """
    Process a single module for a specific date with retry logic.
    
    Returns:
        True if successful, False otherwise
    """
    # Smart skip logic: Check if data has changed in HCSS before fetching
    # 1. Get our cached data count
    # 2. Make a lightweight API call to get current count from HCSS
    # 3. If counts match, skip (no changes)
    # 4. If counts differ, fetch full data (something changed)
    
    from datetime import datetime, timezone
    
    # Check if we have cached data for this date
    if job_type == "timelog":
        from api.database import TimelogVerificationCache
        cached = db.query(TimelogVerificationCache).filter(
            and_(
                TimelogVerificationCache.verification_date == target_date,
                TimelogVerificationCache.business_unit_id == None,
            )
        ).first()
        cached_count = cached.total_timecards if cached else None
        
    elif job_type == "quantity":
        from api.database import QuantityTrackingCache
        cached = db.query(QuantityTrackingCache).filter(
            QuantityTrackingCache.tracking_date == target_date
        ).first()
        cached_count = cached.total_cost_codes if cached else None
        
    elif job_type == "budget":
        from api.database import BudgetTrackingCache
        cached = db.query(BudgetTrackingCache).filter(
            BudgetTrackingCache.tracking_date == target_date
        ).first()
        cached_count = cached.total_cost_codes if cached else None
    else:
        cached_count = None
    
    # If we have cached data, check if HCSS data has changed before doing a full fetch
    if cached_count is not None:
        try:
            from payroll_verification.hcss_client import HCSSClient
            client = HCSSClient()

            # Lightweight call — fetch timecard summaries only (no detail endpoints)
            timecards = client.fetch_timecards(
                start_date=target_date,
                end_date=target_date,
                business_unit_id=None,
            )

            if job_type == "timelog":
                # ---------------------------------------------------------------
                # TIMELOG — no smart skip.
                # Always refetch every run so the DB stays in sync with whatever
                # foremen have submitted or edited in HCSS since the last cycle.
                # Skipping based on timestamps or counts caused missing timecards
                # when HCSS didn't reliably return modification timestamps.
                # ---------------------------------------------------------------
                logger.info(
                    "timelog for %s: always refetching (no skip logic)",
                    target_date,
                )

            elif job_type == "quantity":
                # ---------------------------------------------------------------
                # QUANTITY smart check:
                #
                # HCSS does NOT expose lastModifiedDateTime on cost-code progress
                # endpoints (confirmed from actual API responses).
                #
                # Quantity is derived from timecard activity, so we use the
                # same signal as timelog: compare the latest lastModifiedDateTime
                # across all timecards for this date against our cached updated_at.
                #
                # Decision:
                #   - Any timecard modified after our last fetch → REFETCH
                #   - No timecards modified → SKIP
                #   - No timecards at all (0 results) → SKIP
                #   - HCSS returns no timestamps → fall back to count check
                # ---------------------------------------------------------------
                last_stored_at = cached.updated_at.replace(tzinfo=timezone.utc)

                if not timecards:
                    # No timecards for this date — nothing could have changed
                    logger.info(
                        "✓ %s for %s: no timecards in HCSS, skipping",
                        job_type, target_date,
                    )
                    return True

                # Find latest modification time across all timecards for this date
                latest_hcss_modification = None
                for tc in timecards:
                    for field in ("lastModifiedDateTime", "lockedDateTime", "updatedAt"):
                        raw = tc.get(field)
                        if raw:
                            s = raw.replace("Z", "")
                            if "." in s:
                                s = s.split(".", 1)[0]
                            try:
                                dt = datetime.strptime(s, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
                                if latest_hcss_modification is None or dt > latest_hcss_modification:
                                    latest_hcss_modification = dt
                            except ValueError:
                                pass
                        if latest_hcss_modification:
                            break  # found a valid timestamp for this timecard

                if latest_hcss_modification is None:
                    # No timestamps returned — fall back to timecard count check
                    if len(timecards) == cached_count:
                        logger.info(
                            "✓ %s for %s unchanged (count=%d, no timestamps), skipping",
                            job_type, target_date, cached_count,
                        )
                        return True
                    else:
                        logger.info(
                            "%s for %s count changed: cached=%d HCSS=%d — refetching",
                            job_type, target_date, cached_count, len(timecards),
                        )
                elif latest_hcss_modification <= last_stored_at:
                    logger.info(
                        "✓ %s for %s unchanged (last HCSS edit %s ≤ our fetch %s), skipping",
                        job_type, target_date,
                        latest_hcss_modification.strftime("%Y-%m-%d %H:%M:%S UTC"),
                        last_stored_at.strftime("%Y-%m-%d %H:%M:%S UTC"),
                    )
                    return True
                else:
                    logger.info(
                        "%s for %s: timecard modified %s > our fetch %s — refetching",
                        job_type, target_date,
                        latest_hcss_modification.strftime("%Y-%m-%d %H:%M:%S UTC"),
                        last_stored_at.strftime("%Y-%m-%d %H:%M:%S UTC"),
                    )

            elif job_type == "budget":
                # ---------------------------------------------------------------
                # BUDGET — no smart skip.
                # Budget now uses jobCostsToDate (cumulative) which includes
                # PO, material, and subcontract costs posted without timecards.
                # Timecard timestamps are not a reliable signal for budget changes.
                # Always refetch to ensure data matches the portal.
                # ---------------------------------------------------------------
                logger.info(
                    "budget for %s: always refetching (cumulative data, no skip logic)",
                    target_date,
                )

        except Exception as e:
            # If the check itself fails, always refetch to be safe
            logger.warning(
                "Could not check if %s data changed for %s: %s — will refetch",
                job_type, target_date, str(e),
            )
    else:
        logger.info("No cached data for %s on %s — fetching fresh", job_type, target_date)
    
    # Create job execution record
    job_id = create_cron_job_execution(
        db=db,
        job_type=job_type,
        execution_date=target_date,
        max_retries=MAX_RETRIES,
    )
    
    # Update status to running
    update_cron_job_status(db, job_id, "running")
    
    # Process based on job type
    if job_type == "timelog":
        success, records, error = process_timelog_for_date(db, target_date)
    elif job_type == "quantity":
        success, records, error = process_quantity_for_date(db, target_date)
    elif job_type == "budget":
        success, records, error = process_budget_for_date(db, target_date)
    else:
        logger.error("Unknown job type: %s", job_type)
        return False
    
    # Update job status
    if success:
        update_cron_job_status(
            db=db,
            job_id=job_id,
            status="success",
            records_processed=records,
        )
        return True
    else:
        update_cron_job_status(
            db=db,
            job_id=job_id,
            status="failed",
            error_message=error,
        )
        
        # Schedule retry if not max retries reached
        schedule_retry(db, job_id, RETRY_DELAY_MINUTES)
        logger.warning("Scheduled retry for %s on %s", job_type, target_date)
        return False


def process_all_modules_for_date(db: Session, target_date: str) -> dict[str, bool]:
    """
    Process all three modules for a specific date.
    
    Returns:
        Dict of {module_name: success_status}
    """
    import time
    
    results = {}
    
    for job_type in ["timelog", "quantity", "budget"]:
        try:
            success = process_single_module(db, job_type, target_date)
            results[job_type] = success
            
            # Add delay between modules to avoid rate limiting
            if job_type != "budget":  # Don't delay after the last module
                time.sleep(10)  # 10 second delay between modules
                
        except Exception as exc:
            logger.error("Unexpected error processing %s for %s: %s", job_type, target_date, exc)
            results[job_type] = False
    
    return results


# ===========================================================================
# MAIN CRON JOB LOGIC
# ===========================================================================

def process_pending_retries(db: Session) -> None:
    """
    Process all jobs that are scheduled for retry.
    """
    retry_jobs = get_pending_retry_jobs(db)
    
    if not retry_jobs:
        logger.info("No pending retries")
        return
    
    logger.info("Found %d jobs pending retry", len(retry_jobs))
    
    for job in retry_jobs:
        logger.info(
            "Retrying %s for %s (attempt %d/%d)",
            job.job_type,
            job.execution_date,
            job.attempt_count,
            job.max_retries,
        )
        
        # Update status to running
        update_cron_job_status(db, job.id, "running")
        
        # Process based on job type
        if job.job_type == "timelog":
            success, records, error = process_timelog_for_date(db, job.execution_date)
        elif job.job_type == "quantity":
            success, records, error = process_quantity_for_date(db, job.execution_date)
        elif job.job_type == "budget":
            success, records, error = process_budget_for_date(db, job.execution_date)
        else:
            continue
        
        # Update job status
        if success:
            update_cron_job_status(
                db=db,
                job_id=job.id,
                status="success",
                records_processed=records,
            )
            logger.info("✓ Retry successful for %s on %s", job.job_type, job.execution_date)
        else:
            update_cron_job_status(
                db=db,
                job_id=job.id,
                status="failed",
                error_message=error,
            )
            
            # Schedule another retry if not max retries reached
            if job.attempt_count < job.max_retries:
                schedule_retry(db, job.id, RETRY_DELAY_MINUTES)
                logger.warning("Scheduled another retry for %s on %s", job.job_type, job.execution_date)
            else:
                logger.error("Max retries reached for %s on %s", job.job_type, job.execution_date)
        
        # Delay between retried jobs to avoid rate limiting
        import time
        time.sleep(5)


def get_dates_to_process() -> list[str]:
    """
    Get list of dates that need to be processed.
    Returns dates from yesterday going back LOOKBACK_DAYS.
    """
    today = date.today()
    yesterday = today - timedelta(days=1)
    
    dates = []
    for i in range(LOOKBACK_DAYS):
        target_date = yesterday - timedelta(days=i)
        dates.append(target_date.isoformat())
    
    return dates


def run_unified_cron_job() -> None:
    """
    Main cron job entry point.
    Processes all modules for all dates that need updating.
    """
    logger.info("=" * 80)
    logger.info("UNIFIED CRON JOB STARTED")
    logger.info("=" * 80)
    
    # Ensure tables exist
    create_tables()
    
    # Create database session
    db = SessionLocal()
    
    try:
        # Step 1: Process pending retries first
        logger.info("Step 1: Processing pending retries...")
        process_pending_retries(db)
        
        # Step 2: Get dates to process
        dates_to_process = get_dates_to_process()
        logger.info("Step 2: Checking %d dates for updates...", len(dates_to_process))
        
        # Step 3: Process each date
        total_processed = 0
        total_failed = 0
        
        for target_date in dates_to_process:
            logger.info("-" * 80)
            logger.info("Processing date: %s", target_date)
            
            results = process_all_modules_for_date(db, target_date)
            
            # Count successes and failures
            successes = sum(1 for success in results.values() if success)
            failures = sum(1 for success in results.values() if not success)
            
            total_processed += successes
            total_failed += failures
            
            logger.info(
                "Date %s: %d/%d modules successful",
                target_date,
                successes,
                len(results),
            )
            
            # Add delay between dates to avoid rate limiting
            if target_date != dates_to_process[-1]:  # Don't delay after the last date
                import time
                time.sleep(15)  # 8 second delay between dates
        
        # Summary
        logger.info("=" * 80)
        logger.info("CRON JOB COMPLETED")
        logger.info("Total successful: %d", total_processed)
        logger.info("Total failed: %d", total_failed)
        logger.info("=" * 80)
        
    except Exception as exc:
        logger.exception("Fatal error in cron job: %s", exc)
    finally:
        db.close()


# ===========================================================================
# ENTRY POINT
# ===========================================================================

if __name__ == "__main__":
    run_unified_cron_job()
