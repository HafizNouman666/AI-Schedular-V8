"""
Quantity Tracking routes - uses cache-aside via get_or_fetch_quantity_for_date.
Single DB session per request. No duplicate aggregation logic.
"""
from __future__ import annotations
import logging, uuid
from datetime import date as date_type, datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session
from api.database import SessionLocal, get_db
from api.schemas import ErrorResponse
from notifications.individual_email_sender import send_individual_email
from notifications.individual_email_template import build_individual_email_body
from quantity_tracking.tracker import QuantityResult

router = APIRouter(prefix="/quantity", tags=["Quantity Tracking"])
logger = logging.getLogger(__name__)


# ── Schemas ───────────────────────────────────────────────────────────────────

class QuantityItemRow(BaseModel):
    cost_code_id: str
    cost_code: str
    description: str
    job_id: str
    job_code: str
    unit: str
    cost_type: str = Field(..., description="self_perform | subcontractor")
    planned_quantity: float
    installed_quantity: float
    remaining_quantity: float
    variance: float = Field(..., description="planned_quantity - installed_quantity (positive = remaining, negative = over-run)")
    percent_complete: float
    status: str = Field(..., description="ON_TRACK | NEAR_COMPLETION | OVER_RISK")
    alert: bool

class QuantityCountResponse(BaseModel):
    period_start: str
    period_end: str
    total: int
    on_track: int
    near_completion: int
    over_risk: int

class QuantityVerifyResponse(BaseModel):
    period_start: str
    period_end: str
    total: int
    items: list[QuantityItemRow]

class QuantityNotifyRequest(BaseModel):
    cost_code_id: str
    cost_code: str
    description: str
    job_code: str
    unit: str
    cost_type: str
    planned_quantity: float
    installed_quantity: float
    percent_complete: float
    status: str
    recipients: list[EmailStr] = Field(..., min_length=1)
    comments: str = Field(default="")

class QuantityNotifyResponse(BaseModel):
    cost_code_id: str
    recipients_count: int
    message: str


# ── Helpers ───────────────────────────────────────────────────────────────────

def _validate_date(date_str: str, label: str) -> str:
    try:
        date_type.fromisoformat(date_str)
    except ValueError:
        raise HTTPException(status_code=422,
            detail=f"Invalid {label} \'{date_str}\'. Expected YYYY-MM-DD.")
    return date_str


def _resolve_period(date, start_date, end_date):
    if date:
        d = _validate_date(date, "date")
        return d, d
    if start_date or end_date:
        if not start_date or not end_date:
            raise HTTPException(status_code=422,
                detail="Provide both start_date and end_date, or use date for a single day.")
        return (_validate_date(start_date, "start_date"),
                _validate_date(end_date, "end_date"))
    yesterday = (date_type.today() - timedelta(days=1)).strftime("%Y-%m-%d")
    return yesterday, yesterday


def _date_list(start_date: str, end_date: str) -> list[str]:
    dates, cur = [], datetime.fromisoformat(start_date)
    end = datetime.fromisoformat(end_date)
    while cur <= end:
        dates.append(cur.strftime("%Y-%m-%d"))
        cur += timedelta(days=1)
    return dates


def _aggregate(raw: list[dict]) -> list[QuantityResult]:
    """Aggregate by (job_id, cost_code_id) then recalculate percent + status."""
    agg: dict[tuple, dict] = {}
    for item in raw:
        key = (item["job_id"], item["cost_code_id"])
        if key in agg:
            agg[key]["installed_quantity"] += item["installed_quantity"]
            agg[key]["planned_quantity"] = item["planned_quantity"]
        else:
            agg[key] = dict(item)

    results = []
    for item in agg.values():
        planned   = item["planned_quantity"]
        installed = item["installed_quantity"]
        percent   = round((installed / planned) * 100, 2) if planned > 0 else 0.0
        if percent >= 100.0:
            st = "OVER_RISK"
        elif percent >= 75.0:
            st = "NEAR_COMPLETION"
        else:
            st = "ON_TRACK"
        results.append(QuantityResult(
            cost_code_id=item["cost_code_id"], cost_code=item["cost_code"],
            description=item["description"], job_id=item["job_id"],
            job_code=item["job_code"], unit=item["unit"], cost_type=item["cost_type"],
            planned_quantity=planned, installed_quantity=installed,
            percent_complete=percent, status=st, alert=percent >= 75.0))
    return results


def _fetch_for_range(sd: str, ed: str) -> list[QuantityResult]:
    """
    Fetch quantity data for the exact selected period.

    IMPORTANT:
    Do not fetch each date separately for a range.
    The HCSS Cost Code Summary report is based on the selected date range,
    so the backend must fetch/cache the same selected range as one unit.
    """
    from api.db_services import get_or_fetch_quantity_for_period

    db = SessionLocal()

    try:
        raw = get_or_fetch_quantity_for_period(db, sd, ed)
    finally:
        db.close()

    if not raw:
        raise HTTPException(
            status_code=404,
            detail=f"No quantity data available for {sd} to {ed}.",
        )

    # Data is already calculated for the exact period by tracker.py.
    # No day-by-day aggregation is needed here.
    results: list[QuantityResult] = []

    for item in raw:
        planned = item["planned_quantity"]
        installed = item["installed_quantity"]
        percent = round((installed / planned) * 100, 2) if planned > 0 else 0.0

        if percent >= 100.0:
            st = "OVER_RISK"
        elif percent >= 75.0:
            st = "NEAR_COMPLETION"
        else:
            st = "ON_TRACK"

        results.append(
            QuantityResult(
                cost_code_id=item["cost_code_id"],
                cost_code=item["cost_code"],
                description=item["description"],
                job_id=item["job_id"],
                job_code=item["job_code"],
                unit=item["unit"],
                cost_type=item["cost_type"],
                planned_quantity=planned,
                installed_quantity=installed,
                percent_complete=percent,
                status=st,
                alert=percent >= 75.0,
            )
        )

    return results


def _to_row(r: QuantityResult) -> QuantityItemRow:
    return QuantityItemRow(
        cost_code_id=r.cost_code_id, cost_code=r.cost_code,
        description=r.description, job_id=r.job_id, job_code=r.job_code,
        unit=r.unit, cost_type=r.cost_type,
        planned_quantity=r.planned_quantity, installed_quantity=r.installed_quantity,
        remaining_quantity=r.remaining_quantity,
        variance=round(r.planned_quantity - r.installed_quantity, 4),
        percent_complete=r.percent_complete,
        status=r.status, alert=r.alert)


# ── Route 1: Count ────────────────────────────────────────────────────────────

@router.get("/count", response_model=QuantityCountResponse,
    summary="Summary counts - On Track / Near Completion / Over Risk",
    responses={404: {"description": "No data"}, 502: {"model": ErrorResponse}})
def quantity_count(
    date: str | None = Query(default=None, description="Single date YYYY-MM-DD"),
    start_date: str | None = Query(default=None),
    end_date:   str | None = Query(default=None),
) -> QuantityCountResponse:
    sd, ed = _resolve_period(date, start_date, end_date)
    results = _fetch_for_range(sd, ed)
    total    = len(results)
    on_track = sum(1 for r in results if r.status == "ON_TRACK")
    near     = sum(1 for r in results if r.status == "NEAR_COMPLETION")
    over     = sum(1 for r in results if r.status == "OVER_RISK")
    logger.info("Quantity count: %s to %s total=%d", sd, ed, total)
    return QuantityCountResponse(period_start=sd, period_end=ed,
        total=total, on_track=on_track, near_completion=near, over_risk=over)


# ── Route 2: Verify ───────────────────────────────────────────────────────────

@router.get("/verify", response_model=QuantityVerifyResponse,
    summary="All cost code rows with quantity progress and status",
    responses={404: {"description": "No data"}, 502: {"model": ErrorResponse}})
def quantity_verify(
    date: str | None = Query(default=None, description="Single date YYYY-MM-DD"),
    start_date: str | None = Query(default=None),
    end_date:   str | None = Query(default=None),
    cost_type:     str | None = Query(default=None, description="self_perform | subcontractor"),
    status_filter: str | None = Query(default=None, alias="status",
        description="ON_TRACK | NEAR_COMPLETION | OVER_RISK"),
) -> QuantityVerifyResponse:
    sd, ed = _resolve_period(date, start_date, end_date)
    results = _fetch_for_range(sd, ed)
    if cost_type:
        results = [r for r in results if r.cost_type == cost_type.lower()]
    if status_filter:
        results = [r for r in results if r.status == status_filter.upper()]
    logger.info("Quantity verify: %s to %s returning %d items", sd, ed, len(results))
    return QuantityVerifyResponse(period_start=sd, period_end=ed,
        total=len(results), items=[_to_row(r) for r in results])


# ── Route 3: Notify ───────────────────────────────────────────────────────────

@router.post("/notify", response_model=QuantityNotifyResponse,
    summary="Send email for a single quantity tracking row",
    responses={422: {"model": ErrorResponse}, 502: {"model": ErrorResponse}})
def quantity_notify(payload: QuantityNotifyRequest) -> QuantityNotifyResponse:
    logger.info("Quantity notify: cost_code=%s job=%s status=%s recipients=%d",
        payload.cost_code, payload.job_code, payload.status, len(payload.recipients))
    reasons, flags = [], []
    if payload.status == "OVER_RISK":
        reasons.append(f"Quantity exceeded: {payload.installed_quantity:,.2f} installed "
            f"of {payload.planned_quantity:,.2f} planned {payload.unit} "
            f"({payload.percent_complete:.1f}%)")
    elif payload.status == "NEAR_COMPLETION":
        flags.append(f"Approaching completion: {payload.percent_complete:.1f}% "
            f"({payload.installed_quantity:,.2f} / {payload.planned_quantity:,.2f} {payload.unit})")
    flags.append(f"Cost code type: {payload.cost_type.replace('_', ' ').title()}")
    _, html_body = build_individual_email_body(
        timecard_id=payload.cost_code_id, date=date_type.today().isoformat(),
        job_code=payload.job_code, foreman=f"{payload.cost_code} - {payload.description}",
        status=payload.status, reasons=reasons, flags=flags, comments=payload.comments)
    subject = (f"Quantity Alert - {payload.cost_code} | {payload.description} | "
               f"{payload.job_code} [{payload.status}]")
    recipient_list = [str(r) for r in payload.recipients]
    try:
        send_individual_email(subject=subject, html_body=html_body, recipients=recipient_list)
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=f"Email config error: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Email send failed: {exc}") from exc
    logger.info("Quantity notify sent: cost_code=%s recipients=%d",
        payload.cost_code, len(recipient_list))
    return QuantityNotifyResponse(cost_code_id=payload.cost_code_id,
        recipients_count=len(recipient_list),
        message=f"Alert for {payload.cost_code} sent to {len(recipient_list)} recipient(s).")