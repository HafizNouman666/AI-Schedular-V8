"""
api/database.py
────────────────
CHANGES FROM ORIGINAL:
  + Added ProjectionTrackingCache model   (new — projection tracking module)
  + Added ProjectionTrackingResult model  (new — projection tracking module)

Search for "← NEW" comments to find every change in this file.
All existing code is unchanged.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from pathlib import Path

from sqlalchemy import (
    Boolean, Column, DateTime, Float, Integer,
    String, Text, create_engine, Index, ForeignKey,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker, relationship

_REPO_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(_REPO_ROOT / ".env")

DATABASE_URL = os.getenv("DATABASE_URL", "")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set in .env")

if DATABASE_URL.startswith("postgresql://") or DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


# ===========================================================================
# USER MODEL
# ===========================================================================

class User(Base):
    __tablename__ = "users"

    id             = Column(String, primary_key=True, index=True)
    email          = Column(String, unique=True, index=True, nullable=False)
    full_name      = Column(String, nullable=False)
    hashed_password = Column(String, nullable=False)
    is_active      = Column(Boolean, default=True, nullable=False)
    created_at     = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)


# ===========================================================================
# TIME LOG VERIFICATION MODELS
# ===========================================================================

class TimelogVerificationCache(Base):
    __tablename__ = "timelog_verification_cache"

    id                = Column(String, primary_key=True, index=True)
    verification_date = Column(String, nullable=False, index=True)
    business_unit_id  = Column(String, nullable=True, index=True)

    total_timecards = Column(Integer, nullable=False, default=0)
    approved_count  = Column(Integer, nullable=False, default=0)
    flagged_count   = Column(Integer, nullable=False, default=0)
    rejected_count  = Column(Integer, nullable=False, default=0)

    fetched_at  = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    created_at  = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at  = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                         onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        Index('idx_timelog_date_bu', 'verification_date', 'business_unit_id', unique=True),
    )


class TimelogTimecardResult(Base):
    __tablename__ = "timelog_timecard_results"

    id                = Column(String, primary_key=True, index=True)
    verification_date = Column(String, nullable=False, index=True)
    business_unit_id  = Column(String, nullable=True, index=True)

    timecard_id = Column(String, nullable=False, index=True)
    job_id      = Column(String, nullable=True)
    job_code    = Column(String, nullable=False)
    foreman_id  = Column(String, nullable=True)
    foreman     = Column(String, nullable=False)

    status  = Column(String, nullable=False, index=True)
    reasons = Column(Text, nullable=False, default="")
    flags   = Column(Text, nullable=False, default="")
    why     = Column(Text, nullable=False, default="")

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        Index('idx_timelog_timecard_date_bu', 'verification_date', 'business_unit_id'),
        Index('idx_timelog_timecard_status',  'status'),
        Index('idx_timelog_timecard_id',      'timecard_id'),
    )


# ===========================================================================
# QUANTITY TRACKING MODELS
# ===========================================================================

class QuantityTrackingCache(Base):
    __tablename__ = "quantity_tracking_cache"

    id            = Column(String, primary_key=True, index=True)
    tracking_date = Column(String, nullable=False, index=True)

    total_cost_codes      = Column(Integer, nullable=False, default=0)
    on_track_count        = Column(Integer, nullable=False, default=0)
    near_completion_count = Column(Integer, nullable=False, default=0)
    over_risk_count       = Column(Integer, nullable=False, default=0)

    fetched_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        Index('idx_quantity_date', 'tracking_date', unique=True),
    )


class QuantityTrackingResult(Base):
    __tablename__ = "quantity_tracking_results"

    id            = Column(String, primary_key=True, index=True)
    tracking_date = Column(String, nullable=False, index=True)

    cost_code_id = Column(String, nullable=False, index=True)
    cost_code    = Column(String, nullable=False)
    description  = Column(String, nullable=False)
    job_id       = Column(String, nullable=False, index=True)
    job_code     = Column(String, nullable=False)
    unit         = Column(String, nullable=False)
    cost_type    = Column(String, nullable=False, index=True)

    planned_quantity   = Column(Float, nullable=False, default=0.0)
    installed_quantity = Column(Float, nullable=False, default=0.0)
    remaining_quantity = Column(Float, nullable=False, default=0.0)
    percent_complete   = Column(Float, nullable=False, default=0.0)

    status = Column(String, nullable=False, index=True)
    alert  = Column(Boolean, nullable=False, default=False)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        Index('idx_quantity_date_costcode', 'tracking_date', 'cost_code_id'),
        Index('idx_quantity_date_job',      'tracking_date', 'job_id'),
        Index('idx_quantity_status',        'status'),
        Index('idx_quantity_cost_type',     'cost_type'),
    )


# ===========================================================================
# BUDGET TRACKING MODELS
# ===========================================================================

class BudgetTrackingCache(Base):
    __tablename__ = "budget_tracking_cache"

    id            = Column(String, primary_key=True, index=True)
    tracking_date = Column(String, nullable=False, index=True)

    total_cost_codes = Column(Integer, nullable=False, default=0)
    on_track_count   = Column(Integer, nullable=False, default=0)
    over_risk_count  = Column(Integer, nullable=False, default=0)

    fetched_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        Index('idx_budget_date', 'tracking_date', unique=True),
    )


class BudgetTrackingResult(Base):
    __tablename__ = "budget_tracking_results"

    id            = Column(String, primary_key=True, index=True)
    tracking_date = Column(String, nullable=False, index=True)

    cost_code_id          = Column(String, nullable=False, index=True)
    cost_code             = Column(String, nullable=False)
    cost_code_description = Column(String, nullable=False)
    job_id                = Column(String, nullable=False, index=True)
    job_name              = Column(String, nullable=False)
    business_unit         = Column(String, nullable=True)

    budgeted_all_cost      = Column(Float, nullable=False, default=0.0)
    actual_cost            = Column(Float, nullable=False, default=0.0)
    utilization_percentage = Column(Integer, nullable=True, default=None)
    variance               = Column(Float, nullable=False, default=0.0)
    status                 = Column(String, nullable=False, index=True)

    # ── Actual cost breakdown ─────────────────────────────────────────
    labor_cost        = Column(Float, nullable=False, default=0.0)
    equipment_cost    = Column(Float, nullable=False, default=0.0)
    material_cost     = Column(Float, nullable=False, default=0.0)
    subcontract_cost  = Column(Float, nullable=False, default=0.0)
    trucking_cost     = Column(Float, nullable=False, default=0.0)
    labor_hours       = Column(Float, nullable=False, default=0.0)
    quantity          = Column(Float, nullable=False, default=0.0)

    # ── Planned budget breakdown ──────────────────────────────────────
    labor_budget        = Column(Float, nullable=False, default=0.0)
    equipment_budget    = Column(Float, nullable=False, default=0.0)
    material_budget     = Column(Float, nullable=False, default=0.0)
    subcontract_budget  = Column(Float, nullable=False, default=0.0)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        Index('idx_budget_date_costcode', 'tracking_date', 'cost_code_id'),
        Index('idx_budget_date_job',      'tracking_date', 'job_id'),
        Index('idx_budget_status',        'status'),
    )


# ===========================================================================
# PROJECTION TRACKING MODELS   ← NEW — added for Projection Tracking module
# ===========================================================================

class ProjectionTrackingCache(Base):                                    # ← NEW
    """
    Cache table for monthly projection results.
    One row per tracking_month (YYYY-MM).

    Key difference from budget/quantity caches:
      - tracking_month is YYYY-MM not YYYY-MM-DD
      - Projections are calculated monthly, not daily
      - Has at_risk_count and over_budget_count (3 statuses, not 2)

    Added as part of Projection Tracking module.
    """
    __tablename__ = "projection_tracking_cache"

    id             = Column(String, primary_key=True, index=True)
    tracking_month = Column(String, nullable=False, index=True)   # YYYY-MM

    # Summary counts
    total_jobs        = Column(Integer, nullable=False, default=0)
    on_track_count    = Column(Integer, nullable=False, default=0)
    at_risk_count     = Column(Integer, nullable=False, default=0)
    over_budget_count = Column(Integer, nullable=False, default=0)
    alerts_count      = Column(Integer, nullable=False, default=0)

    # Metadata
    fetched_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        Index('idx_projection_month', 'tracking_month', unique=True),
    )


class ProjectionTrackingResult(Base):                                   # ← NEW
    """
    Per-job projection result for a given month.
    Linked to ProjectionTrackingCache via tracking_month.

    Stores all fields needed by the frontend and email report:
      - Contract values (original + change orders)
      - EAC (projected_final_cost)
      - Cost variance
      - Billing position
      - Status flags

    Added as part of Projection Tracking module.
    """
    __tablename__ = "projection_tracking_results"

    id             = Column(String, primary_key=True, index=True)
    tracking_month = Column(String, nullable=False, index=True)   # YYYY-MM

    # Job identification
    job_id        = Column(String, nullable=False, index=True)
    job_code      = Column(String, nullable=False)
    job_name      = Column(String, nullable=False)
    business_unit = Column(String, nullable=True)



# Production Analysis / cost-code identification
    period_start = Column(String, nullable=True)
    period_end   = Column(String, nullable=True)

    cost_code_id          = Column(String, nullable=False, default="", index=True)
    cost_code             = Column(String, nullable=False, default="")
    cost_code_description = Column(String, nullable=False, default="")
    unit                  = Column(String, nullable=False, default="")

    # Quantity / completion
    budgeted_quantity = Column(Float, nullable=False, default=0.0)
    quantity          = Column(Float, nullable=False, default=0.0)
    completion_pct    = Column(Float, nullable=False, default=0.0)

    # Production Analysis cost fields
    budgeted_cost = Column(Float, nullable=False, default=0.0)
    expected      = Column(Float, nullable=False, default=0.0)
    actual        = Column(Float, nullable=False, default=0.0)
    variance      = Column(Float, nullable=False, default=0.0)

    # Projection fields
    projected_final       = Column(Float, nullable=False, default=0.0)
    projected_over_under  = Column(Float, nullable=False, default=0.0)
    performance_factor    = Column(Float, nullable=False, default=0.0)

    # Actual cost breakdown
    actual_labor_cost       = Column(Float, nullable=False, default=0.0)
    actual_equipment_cost   = Column(Float, nullable=False, default=0.0)
    actual_material_cost    = Column(Float, nullable=False, default=0.0)
    actual_subcontract_cost = Column(Float, nullable=False, default=0.0)
    actual_trucking_cost    = Column(Float, nullable=False, default=0.0)

    # Supporting HCSS values
    quantity_from_job_costs = Column(Float, nullable=False, default=0.0)
    labor_hours            = Column(Float, nullable=False, default=0.0)
    equipment_hours        = Column(Float, nullable=False, default=0.0)



    # Contract
    original_contract_value = Column(Float, nullable=False, default=0.0)
    approved_change_orders  = Column(Float, nullable=False, default=0.0)
    revised_contract_value  = Column(Float, nullable=False, default=0.0)

    # Cost / EAC
    original_budget      = Column(Float, nullable=False, default=0.0)
    actual_cost_to_date  = Column(Float, nullable=False, default=0.0)
    hcss_forecast_cost   = Column(Float, nullable=False, default=0.0)   # ← NEW raw HCSS forecast
    projected_final_cost = Column(Float, nullable=False, default=0.0)
    cost_variance        = Column(Float, nullable=False, default=0.0)
    forecast_source      = Column(String, nullable=False, default="eac_formula")  # ← NEW

    # Progress
    percent_complete             = Column(Float,  nullable=False, default=0.0)
    estimated_completion_month   = Column(String, nullable=True)   # YYYY-MM

    # Billing
    billed_to_date          = Column(Float, nullable=False, default=0.0)
    projected_final_billing = Column(Float, nullable=False, default=0.0)
    billing_variance        = Column(Float, nullable=False, default=0.0)

    # Flags
    status           = Column(String,  nullable=False, index=True)  # ON_TRACK | AT_RISK | OVER_BUDGET
    alert            = Column(Boolean, nullable=False, default=False)
    discrepancy_flag = Column(Boolean, nullable=False, default=False)

    # Metadata
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        Index('idx_projection_month_job',   'tracking_month', 'job_id'),
        Index('idx_projection_month_costcode', 'tracking_month', 'cost_code_id'),
        Index('idx_projection_status',      'status'),
        Index('idx_projection_alert',       'alert'),
        Index('idx_projection_discrepancy', 'discrepancy_flag'),
    )


# ===========================================================================
# CRON JOB TRACKING MODEL
# ===========================================================================

class CronJobExecution(Base):
    __tablename__ = "cron_job_executions"

    id             = Column(String, primary_key=True, index=True)
    job_type       = Column(String, nullable=False, index=True)
    execution_date = Column(String, nullable=False, index=True)

    status        = Column(String,  nullable=False, index=True)
    attempt_count = Column(Integer, nullable=False, default=1)
    max_retries   = Column(Integer, nullable=False, default=3)

    records_processed = Column(Integer, nullable=True)
    error_message     = Column(Text,    nullable=True)

    started_at   = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    next_retry_at = Column(DateTime, nullable=True)
    created_at   = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at   = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                          onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        Index('idx_cron_job_type_date', 'job_type', 'execution_date'),
        Index('idx_cron_status',        'status'),
        Index('idx_cron_next_retry',    'next_retry_at'),
    )


# ===========================================================================
# SCHEDULE MONITORING MODELS
# ===========================================================================

class ScheduleMonitoringCache(Base):
    """
    One row per tracking week (week_end_date YYYY-MM-DD).
    Summary counts for the weekly schedule monitoring run.
    """
    __tablename__ = "schedule_monitoring_cache"

    id             = Column(String, primary_key=True, index=True)
    week_end_date  = Column(String, nullable=False, index=True)   # YYYY-MM-DD (Friday)
    period_start   = Column(String, nullable=False)
    period_end     = Column(String, nullable=False)

    total_jobs          = Column(Integer, nullable=False, default=0)
    jobs_with_critical  = Column(Integer, nullable=False, default=0)
    jobs_with_warnings  = Column(Integer, nullable=False, default=0)
    total_alerts        = Column(Integer, nullable=False, default=0)
    critical_count      = Column(Integer, nullable=False, default=0)
    warning_count       = Column(Integer, nullable=False, default=0)

    fetched_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        Index('idx_schedule_week', 'week_end_date', unique=True),
    )


class ScheduleMonitoringAlert(Base):
    """
    One row per alert generated by the schedule monitoring engine.
    Linked to ScheduleMonitoringCache via week_end_date.
    """
    __tablename__ = "schedule_monitoring_alerts"

    id            = Column(String, primary_key=True, index=True)
    week_end_date = Column(String, nullable=False, index=True)

    job_id        = Column(String, nullable=False, index=True)
    job_code      = Column(String, nullable=False)
    job_name      = Column(String, nullable=False)
    business_unit = Column(String, nullable=True)

    severity      = Column(String, nullable=False, index=True)   # "critical" | "warning"
    alert_type    = Column(String, nullable=False, index=True)
    message       = Column(String, nullable=False)
    cost_code_id  = Column(String, nullable=True)
    cost_code_name = Column(String, nullable=True)
    detail_json   = Column(Text, nullable=False, default="{}")
    generated_at  = Column(String, nullable=False)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        Index('idx_schedule_alert_week',     'week_end_date'),
        Index('idx_schedule_alert_job',      'job_id'),
        Index('idx_schedule_alert_severity', 'severity'),
        Index('idx_schedule_alert_type',     'alert_type'),
    )


class ScheduleMonitoringJobSummary(Base):
    """
    One row per job per week.  Stores the rolled-up job-level status
    and key metrics so the API can return summaries without re-running analysis.
    """
    __tablename__ = "schedule_monitoring_job_summaries"

    id            = Column(String, primary_key=True, index=True)
    week_end_date = Column(String, nullable=False, index=True)

    job_id        = Column(String, nullable=False, index=True)
    job_code      = Column(String, nullable=False)
    job_name      = Column(String, nullable=False)
    business_unit = Column(String, nullable=True)

    status            = Column(String, nullable=False, index=True)  # ON_TRACK|WARNING|CRITICAL
    critical_alerts   = Column(Integer, nullable=False, default=0)
    warning_alerts    = Column(Integer, nullable=False, default=0)

    cost_pct          = Column(Float, nullable=False, default=0.0)
    quantity_pct      = Column(Float, nullable=False, default=0.0)
    total_budget_cost = Column(Float, nullable=False, default=0.0)
    total_actual_cost = Column(Float, nullable=False, default=0.0)
    total_labor_hours = Column(Float, nullable=False, default=0.0)

    days_since_activity  = Column(Integer, nullable=False, default=0)
    last_timecard_date   = Column(String, nullable=True)
    estimated_end_date   = Column(String, nullable=True)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        Index('idx_schedule_summary_week',   'week_end_date'),
        Index('idx_schedule_summary_job',    'job_id'),
        Index('idx_schedule_summary_status', 'status'),
    )


def create_tables() -> None:
    """Create all tables if they don't exist yet.
    Projection + Schedule tables are created automatically because the models
    are registered with Base above.
    """
    Base.metadata.create_all(bind=engine)


def get_db():
    """FastAPI dependency — yields a DB session and closes it after the request."""
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()