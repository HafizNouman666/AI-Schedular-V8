"""
Database service layer - cache-aside pattern for quantity, budget, and projection.
"""
from __future__ import annotations
import json, logging, uuid
from datetime import datetime, timezone
from typing import Any
from sqlalchemy.orm import Session
from sqlalchemy import and_
from api.database import (
    TimelogVerificationCache, TimelogTimecardResult,
    QuantityTrackingCache, QuantityTrackingResult,
    BudgetTrackingCache, BudgetTrackingResult,
    ProjectionTrackingCache,
    ProjectionTrackingResult,
    CronJobExecution,
)
logger = logging.getLogger(__name__)


# ── TIMELOG ──────────────────────────────────────────────────────────────────

def store_timelog_verification(db, verification_date, business_unit_id, results):
    total = len(results)
    approved = sum(1 for r in results if r.get("status") == "APPROVED")
    flagged  = sum(1 for r in results if r.get("status") == "FLAGGED")
    rejected = sum(1 for r in results if r.get("status") == "REJECTED")
    entry = db.query(TimelogVerificationCache).filter(and_(
        TimelogVerificationCache.verification_date == verification_date,
        TimelogVerificationCache.business_unit_id  == business_unit_id,
    )).first()
    if entry:
        entry.total_timecards = total; entry.approved_count = approved
        entry.flagged_count = flagged; entry.rejected_count = rejected
        entry.fetched_at = datetime.now(timezone.utc)
        entry.updated_at = datetime.now(timezone.utc)
        cache_id = entry.id
        db.query(TimelogTimecardResult).filter(and_(
            TimelogTimecardResult.verification_date == verification_date,
            TimelogTimecardResult.business_unit_id  == business_unit_id,
        )).delete()
    else:
        cache_id = str(uuid.uuid4())
        db.add(TimelogVerificationCache(id=cache_id,
            verification_date=verification_date, business_unit_id=business_unit_id,
            total_timecards=total, approved_count=approved,
            flagged_count=flagged, rejected_count=rejected))
    for r in results:
        db.add(TimelogTimecardResult(id=str(uuid.uuid4()),
            verification_date=verification_date, business_unit_id=business_unit_id,
            timecard_id=r.get("id",""), job_id=r.get("job_id"),
            job_code=r.get("job",""), foreman_id=r.get("foreman_id"),
            foreman=r.get("foreman",""), status=r.get("status","APPROVED"),
            reasons=json.dumps(r.get("reasons",[])),
            flags=json.dumps(r.get("flags",[])), why=r.get("why","")))
    db.commit()
    logger.info("Stored timelog: date=%s records=%d", verification_date, total)
    return cache_id, total


def get_timelog_verification(db, verification_date, business_unit_id=None):
    entry = db.query(TimelogVerificationCache).filter(and_(
        TimelogVerificationCache.verification_date == verification_date,
        TimelogVerificationCache.business_unit_id  == business_unit_id,
    )).first()
    if not entry:
        return None, []
    summary = {"date": verification_date, "total": entry.total_timecards,
        "approved": entry.approved_count, "flagged": entry.flagged_count,
        "rejected": entry.rejected_count, "fetched_at": entry.fetched_at.isoformat()}
    rows = db.query(TimelogTimecardResult).filter(and_(
        TimelogTimecardResult.verification_date == verification_date,
        TimelogTimecardResult.business_unit_id  == business_unit_id,
    )).all()
    results = [{"id": tc.timecard_id, "date": verification_date,
        "job": tc.job_code, "job_id": tc.job_id, "foreman": tc.foreman,
        "foreman_id": tc.foreman_id, "status": tc.status,
        "reasons": json.loads(tc.reasons), "flags": json.loads(tc.flags),
        "why": tc.why} for tc in rows]
    return summary, results


# ── QUANTITY ─────────────────────────────────────────────────────────────────

def store_quantity_tracking(db, tracking_date, results):
    total    = len(results)
    on_track = sum(1 for r in results if r.get("status") == "ON_TRACK")
    near     = sum(1 for r in results if r.get("status") == "NEAR_COMPLETION")
    over     = sum(1 for r in results if r.get("status") == "OVER_RISK")
    entry = db.query(QuantityTrackingCache).filter(
        QuantityTrackingCache.tracking_date == tracking_date).first()
    if entry:
        entry.total_cost_codes = total; entry.on_track_count = on_track
        entry.near_completion_count = near; entry.over_risk_count = over
        entry.fetched_at = datetime.now(timezone.utc)
        entry.updated_at = datetime.now(timezone.utc)
        cache_id = entry.id
        db.query(QuantityTrackingResult).filter(
            QuantityTrackingResult.tracking_date == tracking_date).delete()
    else:
        cache_id = str(uuid.uuid4())
        db.add(QuantityTrackingCache(id=cache_id, tracking_date=tracking_date,
            total_cost_codes=total, on_track_count=on_track,
            near_completion_count=near, over_risk_count=over))
    for r in results:
        db.add(QuantityTrackingResult(id=str(uuid.uuid4()),
            tracking_date=tracking_date,
            cost_code_id=r.get("cost_code_id",""), cost_code=r.get("cost_code",""),
            description=r.get("description",""), job_id=r.get("job_id",""),
            job_code=r.get("job_code",""), unit=r.get("unit",""),
            cost_type=r.get("cost_type",""),
            planned_quantity=float(r.get("planned_quantity",0)),
            installed_quantity=float(r.get("installed_quantity",0)),
            remaining_quantity=float(r.get("remaining_quantity",0)),
            percent_complete=float(r.get("percent_complete",0)),
            status=r.get("status","ON_TRACK"), alert=r.get("alert",False)))
    db.commit()
    logger.info("Stored quantity: date=%s records=%d", tracking_date, total)
    return cache_id, total


def get_quantity_tracking(db, tracking_date):
    entry = db.query(QuantityTrackingCache).filter(
        QuantityTrackingCache.tracking_date == tracking_date).first()
    if not entry:
        return None, []
    summary = {"date": tracking_date, "total": entry.total_cost_codes,
        "on_track": entry.on_track_count, "near_completion": entry.near_completion_count,
        "over_risk": entry.over_risk_count, "fetched_at": entry.fetched_at.isoformat()}
    rows = db.query(QuantityTrackingResult).filter(
        QuantityTrackingResult.tracking_date == tracking_date).all()
    results = [{"cost_code_id": r.cost_code_id, "cost_code": r.cost_code,
        "description": r.description, "job_id": r.job_id, "job_code": r.job_code,
        "unit": r.unit, "cost_type": r.cost_type,
        "planned_quantity": r.planned_quantity, "installed_quantity": r.installed_quantity,
        "remaining_quantity": r.remaining_quantity, "percent_complete": r.percent_complete,
        "status": r.status, "alert": r.alert} for r in rows]
    return summary, results


# ── BUDGET ───────────────────────────────────────────────────────────────────

def store_budget_tracking(db, tracking_date, results):
    total    = len(results)
    on_track = sum(1 for r in results if r.get("status") == "ON_TRACK")
    over     = sum(1 for r in results if r.get("status") == "OVER_RISK")
    entry = db.query(BudgetTrackingCache).filter(
        BudgetTrackingCache.tracking_date == tracking_date).first()
    if entry:
        entry.total_cost_codes = total; entry.on_track_count = on_track
        entry.over_risk_count = over
        entry.fetched_at = datetime.now(timezone.utc)
        entry.updated_at = datetime.now(timezone.utc)
        cache_id = entry.id
        db.query(BudgetTrackingResult).filter(
            BudgetTrackingResult.tracking_date == tracking_date).delete()
    else:
        cache_id = str(uuid.uuid4())
        db.add(BudgetTrackingCache(id=cache_id, tracking_date=tracking_date,
            total_cost_codes=total, on_track_count=on_track, over_risk_count=over))
    for r in results:
        raw_util = r.get("utilization_percentage")
        util_val = int(raw_util) if raw_util is not None else None
        db.add(BudgetTrackingResult(
            id=str(uuid.uuid4()),
            tracking_date=tracking_date,
            cost_code_id=r.get("cost_code_id", ""),
            cost_code=r.get("cost_code", ""),
            cost_code_description=r.get("cost_code_description", ""),
            job_id=r.get("job_id", ""),
            job_name=r.get("job_name", ""),
            business_unit=r.get("business_unit"),
            # Store raw HCSS total budget — expected_budget is computed on the fly
            budgeted_all_cost=float(r.get("budgeted_all_cost", 0)),
            actual_cost=float(r.get("actual_cost", 0)),
            # utilization_percentage / variance / status are computed at response time
            # and not stored — these DB columns are not relied on by any route
            utilization_percentage=util_val,
            variance=float(r.get("variance", 0)),
            status=r.get("status", "ON_TRACK"),
            labor_cost=float(r.get("labor_cost", 0)),
            equipment_cost=float(r.get("equipment_cost", 0)),
            material_cost=float(r.get("material_cost", 0)),
            subcontract_cost=float(r.get("subcontract_cost", 0)),
            trucking_cost=float(r.get("trucking_cost", 0)),
            labor_hours=float(r.get("labor_hours", 0)),
            quantity=float(r.get("quantity", 0)),
            labor_budget=float(r.get("labor_budget", 0)),
            equipment_budget=float(r.get("equipment_budget", 0)),
            material_budget=float(r.get("material_budget", 0)),
            subcontract_budget=float(r.get("subcontract_budget", 0)),
        ))
    db.commit()
    logger.info("Stored budget: date=%s records=%d", tracking_date, total)
    return cache_id, total


def get_budget_tracking(db, tracking_date):
    entry = db.query(BudgetTrackingCache).filter(
        BudgetTrackingCache.tracking_date == tracking_date).first()
    if not entry:
        return None, []
    summary = {"date": tracking_date, "total": entry.total_cost_codes,
        "on_track": entry.on_track_count, "over_risk": entry.over_risk_count,
        "fetched_at": entry.fetched_at.isoformat()}
    rows = db.query(BudgetTrackingResult).filter(
        BudgetTrackingResult.tracking_date == tracking_date).all()
    results = [{
        "cost_code_id":          r.cost_code_id,
        "cost_code":             r.cost_code,
        "cost_code_description": r.cost_code_description,
        "job_id":                r.job_id,
        "job_name":              r.job_name,
        "business_unit":         r.business_unit,
        # budgeted_all_cost is the raw HCSS total — expected_budget computed on the fly
        "budgeted_all_cost":     r.budgeted_all_cost,
        "actual_cost":           r.actual_cost,
        # utilization/variance/status are NOT read from DB — computed at response time
        "labor_cost":       getattr(r, "labor_cost",       0.0) or 0.0,
        "equipment_cost":   getattr(r, "equipment_cost",   0.0) or 0.0,
        "material_cost":    getattr(r, "material_cost",    0.0) or 0.0,
        "subcontract_cost": getattr(r, "subcontract_cost", 0.0) or 0.0,
        "trucking_cost":    getattr(r, "trucking_cost",    0.0) or 0.0,
        "labor_hours":      getattr(r, "labor_hours",      0.0) or 0.0,
        "quantity":         getattr(r, "quantity",         0.0) or 0.0,
        "labor_budget":     getattr(r, "labor_budget",     0.0) or 0.0,
        "equipment_budget": getattr(r, "equipment_budget", 0.0) or 0.0,
        "material_budget":  getattr(r, "material_budget",  0.0) or 0.0,
        "subcontract_budget": getattr(r, "subcontract_budget", 0.0) or 0.0,
        "foremen": [],
    } for r in rows]
    return summary, results


# ── CACHE-ASIDE HELPERS ──────────────────────────────────────────────────────

def get_or_fetch_quantity_for_date(db: Session, target_date: str) -> list[dict]:
    _, cached = get_quantity_tracking(db, target_date)
    if cached:
        logger.info("Quantity DB HIT  date=%s records=%d", target_date, len(cached))
        return cached

    logger.info("Quantity DB MISS date=%s  -> fetching HCSS", target_date)
    try:
        from quantity_tracking.tracker import track_quantities
        hcss = track_quantities(target_date=target_date)
        if not hcss:
            logger.info("HCSS returned 0 quantity records for date=%s", target_date)
            return []
        dicts = [{"cost_code_id": r.cost_code_id, "cost_code": r.cost_code,
            "description": r.description, "job_id": r.job_id,
            "job_code": r.job_code, "unit": r.unit,
            "cost_type": r.cost_type, "planned_quantity": r.planned_quantity,
            "installed_quantity": r.installed_quantity,
            "remaining_quantity": r.remaining_quantity,
            "percent_complete": r.percent_complete,
            "status": r.status, "alert": r.alert} for r in hcss]
        store_quantity_tracking(db, target_date, dicts)
        logger.info("Quantity stored from HCSS date=%s records=%d", target_date, len(dicts))
        return dicts
    except Exception as exc:
        logger.error("Quantity HCSS fetch failed date=%s: %s", target_date, exc)
        return []


def get_or_fetch_quantity_for_period(db: Session, start_date: str, end_date: str) -> list[dict]:
    cache_key = start_date if start_date == end_date else f"{start_date}__{end_date}"

    _, cached = get_quantity_tracking(db, cache_key)
    if cached:
        logger.info("Quantity DB HIT  period=%s to %s key=%s records=%d", start_date, end_date, cache_key, len(cached))
        return cached

    logger.info("Quantity DB MISS period=%s to %s key=%s -> fetching HCSS", start_date, end_date, cache_key)
    try:
        from quantity_tracking.tracker import track_quantities

        hcss = track_quantities(start_date=start_date, end_date=end_date)

        if not hcss:
            logger.info("HCSS returned 0 quantity records for period=%s to %s", start_date, end_date)
            return []

        dicts = [
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
            for r in hcss
        ]

        store_quantity_tracking(db, cache_key, dicts)
        logger.info("Quantity stored from HCSS period=%s to %s key=%s records=%d", start_date, end_date, cache_key, len(dicts))
        return dicts

    except Exception as exc:
        logger.error("Quantity HCSS fetch failed period=%s to %s: %s", start_date, end_date, exc)
        return []


def get_or_fetch_budget_for_date(db: Session, target_date: str) -> list[dict]:
    _, cached = get_budget_tracking(db, target_date)
    if cached:
        logger.info("Budget DB HIT  date=%s records=%d", target_date, len(cached))
        return cached

    logger.info("Budget DB MISS date=%s  -> fetching HCSS", target_date)
    try:
        from budget_tracking.hcss_client import BudgetHCSSClient
        from budget_tracking.analyzer import calculate_budget_status

        raw = BudgetHCSSClient().fetch_budget_summary(date=target_date)
        if not raw:
            logger.info("HCSS returned 0 budget records for date=%s", target_date)
            return []

        budget_results = calculate_budget_status(raw)
        raw_by_key: dict[tuple, dict] = {}
        for item in raw:
            jid  = (item.get("job")      or {}).get("jobId",      "")
            ccid = (item.get("costCode") or {}).get("costCodeId", "")
            if jid and ccid:
                raw_by_key[(jid, ccid)] = item

        dicts: list[dict] = []
        for r in budget_results:
            extra = raw_by_key.get((r.job_id, r.cost_code_id), {})
            dicts.append({
                "cost_code_id":          r.cost_code_id,
                "cost_code":             r.cost_code,
                "cost_code_description": r.cost_code_description,
                "job_id":                r.job_id,
                "job_name":              r.job_name,
                "business_unit":         r.business_unit,
                "budgeted_all_cost":     r.budgeted_all_cost,
                "actual_cost":           r.actual_cost,
                "labor_cost":       extra.get("laborCost",       0.0),
                "equipment_cost":   extra.get("equipmentCost",   0.0),
                "material_cost":    extra.get("materialCost",    0.0),
                "subcontract_cost": extra.get("subcontractCost", 0.0),
                "trucking_cost":    extra.get("truckingCost",    0.0),
                "labor_hours":      extra.get("laborHours",      0.0),
                "quantity":         extra.get("quantity",        0.0),
                "labor_budget":       extra.get("laborBudget",       0.0),
                "equipment_budget":   extra.get("equipmentBudget",   0.0),
                "material_budget":    extra.get("materialBudget",    0.0),
                "subcontract_budget": extra.get("subcontractBudget", 0.0),
                "foremen":            extra.get("foremen",           []),
            })

        store_budget_tracking(db, target_date, dicts)
        logger.info("Budget stored from HCSS date=%s records=%d", target_date, len(dicts))
        return dicts
    except Exception as exc:
        logger.error("Budget HCSS fetch failed date=%s: %s", target_date, exc)
        return []


# ── CRON JOB TRACKING ────────────────────────────────────────────────────────

def create_cron_job_execution(db, job_type, execution_date, max_retries=3):
    existing = db.query(CronJobExecution).filter(and_(
        CronJobExecution.job_type == job_type,
        CronJobExecution.execution_date == execution_date,
        CronJobExecution.status.in_(["pending", "failed", "retrying", "running"]),
    )).first()

    if existing:
        existing.status = "pending"
        existing.error_message = None
        existing.records_processed = None
        existing.started_at = None
        existing.completed_at = None
        existing.next_retry_at = None
        existing.updated_at = datetime.now(timezone.utc)
        db.commit()
        logger.debug("Reusing existing cron job record %s for %s/%s", existing.id[:8], job_type, execution_date)
        return existing.id

    job_id = str(uuid.uuid4())
    db.add(CronJobExecution(id=job_id, job_type=job_type,
        execution_date=execution_date, status="pending",
        attempt_count=1, max_retries=max_retries))
    db.commit()
    return job_id


def update_cron_job_status(db, job_id, status, records_processed=None, error_message=None):
    job = db.query(CronJobExecution).filter(CronJobExecution.id == job_id).first()
    if not job:
        logger.warning("Cron job not found: %s", job_id)
        return
    job.status = status
    if records_processed is not None:
        job.records_processed = records_processed
    if error_message:
        job.error_message = error_message
    if status == "running" and not job.started_at:
        job.started_at = datetime.now(timezone.utc)
    elif status in ("success", "failed"):
        job.completed_at = datetime.now(timezone.utc)
    job.updated_at = datetime.now(timezone.utc)
    db.commit()


def get_pending_retry_jobs(db):
    now = datetime.now(timezone.utc)
    return db.query(CronJobExecution).filter(and_(
        CronJobExecution.status == "retrying",
        CronJobExecution.next_retry_at <= now,
        CronJobExecution.attempt_count < CronJobExecution.max_retries,
    )).all()


def schedule_retry(db: Session, job_id: str, retry_delay_minutes: int = 30) -> None:
    job_execution = db.query(CronJobExecution).filter(CronJobExecution.id == job_id).first()
    if not job_execution:
        return

    from datetime import timedelta

    delay_multiplier = 2 ** (job_execution.attempt_count - 1)
    actual_delay = retry_delay_minutes * delay_multiplier

    job_execution.status = "retrying"
    job_execution.attempt_count += 1
    job_execution.next_retry_at = datetime.now(timezone.utc) + timedelta(minutes=actual_delay)
    job_execution.updated_at = datetime.now(timezone.utc)
    db.commit()

    logger.info("Scheduled retry for job %s: attempt %d/%d in %d minutes",
        job_id, job_execution.attempt_count, job_execution.max_retries, actual_delay)


def check_if_date_processed(db, job_type, execution_date):
    return db.query(CronJobExecution).filter(and_(
        CronJobExecution.job_type == job_type,
        CronJobExecution.execution_date == execution_date,
        CronJobExecution.status == "success",
    )).first() is not None


# ── PROJECTION TRACKING ──────────────────────────────────────────────────────

def _projection_cache_key(start_date: str, end_date: str) -> str:
    return start_date if start_date == end_date else f"{start_date}__{end_date}"


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def store_projection_tracking(db, tracking_month: str, results: list) -> tuple:
    """
    Upsert projection data into the database.

    tracking_month is kept for compatibility, but it can now be either:
        - YYYY-MM for monthly cache
        - YYYY-MM-DD__YYYY-MM-DD for custom date-range cache
    """
    total = len(results)
    on_track = sum(1 for r in results if r.get("status") == "ON_TRACK")
    at_risk = sum(1 for r in results if r.get("status") == "AT_RISK")
    over_budget = sum(1 for r in results if r.get("status") == "OVER_BUDGET")
    alerts = sum(1 for r in results if r.get("alert", False))

    entry = db.query(ProjectionTrackingCache).filter(
        ProjectionTrackingCache.tracking_month == tracking_month
    ).first()

    if entry:
        entry.total_jobs = total
        entry.on_track_count = on_track
        entry.at_risk_count = at_risk
        entry.over_budget_count = over_budget
        entry.alerts_count = alerts
        entry.fetched_at = datetime.now(timezone.utc)
        entry.updated_at = datetime.now(timezone.utc)
        cache_id = entry.id
        db.query(ProjectionTrackingResult).filter(
            ProjectionTrackingResult.tracking_month == tracking_month
        ).delete()
    else:
        cache_id = str(uuid.uuid4())
        db.add(ProjectionTrackingCache(
            id=cache_id,
            tracking_month=tracking_month,
            total_jobs=total,
            on_track_count=on_track,
            at_risk_count=at_risk,
            over_budget_count=over_budget,
            alerts_count=alerts,
        ))

    for r in results:
        db.add(ProjectionTrackingResult(
            id=str(uuid.uuid4()),
            tracking_month=tracking_month,
            period_start=r.get("period_start", ""),
            period_end=r.get("period_end", ""),
            job_id=r.get("job_id", ""),
            job_code=r.get("job_code", ""),
            job_name=r.get("job_name", ""),
            business_unit=r.get("business_unit"),
            cost_code_id=r.get("cost_code_id", ""),
            cost_code=r.get("cost_code", ""),
            cost_code_description=r.get("cost_code_description", ""),
            unit=r.get("unit", ""),
            budgeted_quantity=_safe_float(r.get("budgeted_quantity")),
            quantity=_safe_float(r.get("quantity")),
            completion_pct=_safe_float(r.get("completion_pct")),
            budgeted_cost=_safe_float(r.get("budgeted_cost")),
            expected=_safe_float(r.get("expected")),
            actual=_safe_float(r.get("actual")),
            variance=_safe_float(r.get("variance")),
            projected_final=_safe_float(r.get("projected_final")),
            projected_over_under=_safe_float(r.get("projected_over_under")),
            performance_factor=_safe_float(r.get("performance_factor")),
            actual_labor_cost=_safe_float(r.get("actual_labor_cost")),
            actual_equipment_cost=_safe_float(r.get("actual_equipment_cost")),
            actual_material_cost=_safe_float(r.get("actual_material_cost")),
            actual_subcontract_cost=_safe_float(r.get("actual_subcontract_cost")),
            actual_trucking_cost=_safe_float(r.get("actual_trucking_cost")),
            quantity_from_job_costs=_safe_float(r.get("quantity_from_job_costs")),
            labor_hours=_safe_float(r.get("labor_hours")),
            equipment_hours=_safe_float(r.get("equipment_hours")),
            original_contract_value=0.0,
            approved_change_orders=0.0,
            revised_contract_value=0.0,
            original_budget=_safe_float(r.get("budgeted_cost")),
            actual_cost_to_date=_safe_float(r.get("actual")),
            hcss_forecast_cost=_safe_float(r.get("expected")),
            projected_final_cost=_safe_float(r.get("projected_final")),
            cost_variance=_safe_float(r.get("projected_over_under")),
            forecast_source="production_analysis",
            percent_complete=_safe_float(r.get("completion_pct")),
            estimated_completion_month=r.get("period_end", "")[:7],
            billed_to_date=0.0,
            projected_final_billing=0.0,
            billing_variance=0.0,
            status=r.get("status", "ON_TRACK"),
            alert=r.get("alert", False),
            discrepancy_flag=r.get("discrepancy_flag", False),
        ))

    db.commit()
    logger.info("Stored projection: key=%s records=%d", tracking_month, total)
    return cache_id, total


def get_projection_tracking(db, tracking_month: str) -> tuple:
    entry = db.query(ProjectionTrackingCache).filter(
        ProjectionTrackingCache.tracking_month == tracking_month
    ).first()

    if not entry:
        return None, []

    summary = {
        "key": tracking_month,
        "month": tracking_month,
        "total": entry.total_jobs,
        "on_track": entry.on_track_count,
        "at_risk": entry.at_risk_count,
        "over_budget": entry.over_budget_count,
        "alerts": entry.alerts_count,
        "fetched_at": entry.fetched_at.isoformat(),
    }

    rows = db.query(ProjectionTrackingResult).filter(
        ProjectionTrackingResult.tracking_month == tracking_month
    ).all()

    results = [
        {
            "period_start": getattr(r, "period_start", "") or "",
            "period_end": getattr(r, "period_end", "") or "",
            "job_id": r.job_id,
            "job_code": r.job_code,
            "job_name": r.job_name,
            "business_unit": r.business_unit,
            "cost_code_id": getattr(r, "cost_code_id", "") or "",
            "cost_code": getattr(r, "cost_code", "") or "",
            "cost_code_description": getattr(r, "cost_code_description", "") or "",
            "unit": getattr(r, "unit", "") or "",
            "budgeted_quantity": getattr(r, "budgeted_quantity", 0.0) or 0.0,
            "quantity": getattr(r, "quantity", 0.0) or 0.0,
            "completion_pct": getattr(r, "completion_pct", 0.0) or 0.0,
            "budgeted_cost": getattr(r, "budgeted_cost", 0.0) or 0.0,
            "expected": getattr(r, "expected", 0.0) or 0.0,
            "actual": getattr(r, "actual", 0.0) or 0.0,
            "variance": getattr(r, "variance", 0.0) or 0.0,
            "projected_final": getattr(r, "projected_final", 0.0) or 0.0,
            "projected_over_under": getattr(r, "projected_over_under", 0.0) or 0.0,
            "performance_factor": getattr(r, "performance_factor", 0.0) or 0.0,
            "actual_labor_cost": getattr(r, "actual_labor_cost", 0.0) or 0.0,
            "actual_equipment_cost": getattr(r, "actual_equipment_cost", 0.0) or 0.0,
            "actual_material_cost": getattr(r, "actual_material_cost", 0.0) or 0.0,
            "actual_subcontract_cost": getattr(r, "actual_subcontract_cost", 0.0) or 0.0,
            "actual_trucking_cost": getattr(r, "actual_trucking_cost", 0.0) or 0.0,
            "quantity_from_job_costs": getattr(r, "quantity_from_job_costs", 0.0) or 0.0,
            "labor_hours": getattr(r, "labor_hours", 0.0) or 0.0,
            "equipment_hours": getattr(r, "equipment_hours", 0.0) or 0.0,
            "status": r.status,
            "alert": r.alert,
            "discrepancy_flag": r.discrepancy_flag,
        }
        for r in rows
    ]

    return summary, results


def get_or_fetch_projection_for_period(db, start_date: str, end_date: str) -> list:
    cache_key = _projection_cache_key(start_date, end_date)

    _, cached = get_projection_tracking(db, cache_key)
    if cached:
        logger.info("Projection DB HIT period=%s to %s key=%s records=%d", start_date, end_date, cache_key, len(cached))
        return cached

    logger.info("Projection DB MISS period=%s to %s key=%s -> fetching HCSS", start_date, end_date, cache_key)

    try:
        from projection_tracking.hcss_client import ProjectionHCSSClient
        from projection_tracking.analyzer import calculate_projections
        from projection_tracking.reporting import format_projection_items

        raw = ProjectionHCSSClient().fetch_projection_data(start_date=start_date, end_date=end_date)
        if not raw:
            logger.info("HCSS returned 0 projection records for period=%s to %s", start_date, end_date)
            return []

        results = calculate_projections(raw)
        dicts = format_projection_items(results)

        store_projection_tracking(db, cache_key, dicts)
        logger.info("Projection stored from HCSS period=%s to %s key=%s records=%d", start_date, end_date, cache_key, len(dicts))
        return dicts

    except Exception as exc:
        logger.error("Projection HCSS fetch failed period=%s to %s: %s", start_date, end_date, exc)
        return []


def get_or_fetch_projection_for_month(db, target_month: str) -> list:
    _, cached = get_projection_tracking(db, target_month)
    if cached:
        logger.info("Projection DB HIT month=%s records=%d", target_month, len(cached))
        return cached

    logger.info("Projection DB MISS month=%s -> fetching HCSS", target_month)

    try:
        from projection_tracking.hcss_client import ProjectionHCSSClient
        from projection_tracking.analyzer import calculate_projections
        from projection_tracking.reporting import format_projection_items

        raw = ProjectionHCSSClient().fetch_projection_data(month=target_month)
        if not raw:
            logger.info("HCSS returned 0 projection records for month=%s", target_month)
            return []

        results = calculate_projections(raw)
        dicts = format_projection_items(results)

        store_projection_tracking(db, target_month, dicts)
        logger.info("Projection stored from HCSS month=%s records=%d", target_month, len(dicts))
        return dicts

    except Exception as exc:
        logger.error("Projection HCSS fetch failed month=%s: %s", target_month, exc)
        return []
