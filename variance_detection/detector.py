"""
Variance Detection — core engine.

Reads directly from the existing budget_tracking_results and
quantity_tracking_results DB tables (no new HCSS calls, no new tables).

Cost Variance formula:
    expected_budget       = (installed_quantity / planned_quantity) * budgeted_all_cost
    cost_variance_amount  = expected_budget - actual_cost
    cost_variance_pct     = (actual_cost / expected_budget) * 100   [utilization]
    Risk threshold        : utilization >= 75 % → OVER_RISK
    Sign convention       : positive = budget remaining, negative = over budget

    expected_budget is ZERO when no quantity data is available for the cost code.
    All metrics (variance, utilization, status) follow from the computed expected_budget.

Quantity Variance formula:
    qty_variance_amount   = planned_quantity - installed_quantity
    qty_variance_pct      = (installed_quantity / planned_quantity) * 100
    Risk thresholds       : >= 75 % → NEAR_COMPLETION
                            >= 100 % → OVER_RISK
    Sign convention       : positive = quantity remaining, negative = over-run

Billing:
    Stub — returns empty list with not_implemented flag.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from api.database import BudgetTrackingResult, QuantityTrackingResult

logger = logging.getLogger(__name__)

# ── Thresholds ────────────────────────────────────────────────────────────────

BUDGET_RISK_THRESHOLD = 75.0
QTY_NEAR_THRESHOLD = 75.0
QTY_OVER_THRESHOLD = 100.0


# ── Result dataclasses ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CostVarianceItem:
    cost_code_id: str
    cost_code: str
    cost_code_description: str
    job_id: str
    job_name: str
    business_unit: str
    budgeted_all_cost: float       # raw HCSS total — informational only
    expected_budget: float         # (installed_qty / planned_qty) * budgeted_all_cost
    actual_cost: float
    cost_variance_amount: float    # expected_budget - actual_cost
    cost_variance_pct: float | None
    loss_amount: float
    loss_pct: float | None
    status: str


@dataclass(frozen=True)
class QuantityVarianceItem:
    cost_code_id: str
    cost_code: str
    description: str
    job_id: str
    job_code: str
    unit: str
    cost_type: str
    planned_quantity: float
    installed_quantity: float
    remaining_quantity: float
    qty_variance_amount: float
    qty_variance_pct: float
    status: str
    alert: bool


@dataclass
class VarianceSummary:
    period_start: str
    period_end: str
    total_items: int
    at_risk: int
    total_expected_budget: float = 0.0
    total_actual_cost: float = 0.0
    total_cost_variance: float = 0.0
    near_completion: int = 0
    over_risk: int = 0


# ── Date helpers ──────────────────────────────────────────────────────────────

def _date_list(start_date: str, end_date: str) -> list[str]:
    dates: list[str] = []
    cur = datetime.fromisoformat(start_date)
    end = datetime.fromisoformat(end_date)
    while cur <= end:
        dates.append(cur.strftime("%Y-%m-%d"))
        cur += timedelta(days=1)
    return dates


# ── Shared metric computation ─────────────────────────────────────────────────

def _compute_expected_budget(
    budgeted_all_cost: float,
    planned_qty: float,
    actual_qty: float,
) -> float:
    """
    expected_budget = (actual_qty / planned_qty) * budgeted_all_cost

    actual_qty  = quantity from budget_tracking_results (v1/jobCosts timecard qty)
    planned_qty = planned_quantity from quantity_tracking_results

    Returns 0.0 when planned_qty is 0, actual_qty is 0, or budgeted_all_cost is 0.
    Never falls back to budgeted_all_cost.
    """
    if planned_qty <= 0 or actual_qty <= 0 or budgeted_all_cost <= 0:
        return 0.0
    return round((actual_qty / planned_qty) * budgeted_all_cost, 2)


def _calc_cost_metrics(
    budgeted_all_cost: float,
    expected_budget: float,
    actual_cost: float,
) -> CostVarianceItem | dict:
    """Return computed metrics dict from expected_budget and actual_cost."""
    utilization = (actual_cost / expected_budget * 100) if expected_budget > 0 else None
    variance_amount = expected_budget - actual_cost
    loss_amount = max(0.0, actual_cost - expected_budget)
    loss_pct = (loss_amount / expected_budget * 100) if expected_budget > 0 else None

    if expected_budget <= 0:
        status = "OVER_RISK" if actual_cost > 0 else "ON_TRACK"
    elif utilization >= BUDGET_RISK_THRESHOLD:
        status = "OVER_RISK"
    else:
        status = "ON_TRACK"

    return dict(
        budgeted_all_cost=budgeted_all_cost,
        expected_budget=expected_budget,
        actual_cost=actual_cost,
        cost_variance_amount=variance_amount,
        cost_variance_pct=utilization,
        loss_amount=loss_amount,
        loss_pct=loss_pct,
        status=status,
    )


# ── Aggregation helpers ───────────────────────────────────────────────────────

def _aggregate_cost_rows(
    rows: list[BudgetTrackingResult],
    qty_rows: list[QuantityTrackingResult],
) -> list[CostVarianceItem]:
    """
    Aggregate budget rows by (job_id, cost_code_id), then compute
    expected_budget on the fly.

    expected_budget = (quantity / planned_quantity) * budgeted_all_cost

    Where:
      quantity       = summed from budget_tracking_results (v1/jobCosts timecard qty)
      planned_quantity = from quantity_tracking_results (costCode/progress planned qty)

    Zero when no qty data available — all metrics follow from expected_budget.
    """
    # Build qty lookup for planned_quantity only
    qty_map: dict[tuple, QuantityTrackingResult] = {}
    for q in qty_rows:
        qty_map[(q.job_id, q.cost_code_id)] = q
        qty_map.setdefault((q.job_id, q.cost_code), q)

    # Aggregate actual costs and quantities across dates
    agg: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (row.job_id, row.cost_code_id)
        if key in agg:
            agg[key]["actual_cost"] += row.actual_cost or 0.0
            agg[key]["quantity"]    += row.quantity    or 0.0
        else:
            agg[key] = {
                "cost_code_id":          row.cost_code_id,
                "cost_code":             row.cost_code,
                "cost_code_description": row.cost_code_description,
                "job_id":                row.job_id,
                "job_name":              row.job_name,
                "business_unit":         row.business_unit or "N/A",
                "budgeted_all_cost":     row.budgeted_all_cost or 0.0,
                "actual_cost":           row.actual_cost or 0.0,
                "quantity":              row.quantity    or 0.0,
            }

    results = []
    for key, d in agg.items():
        # planned_quantity from quantity tracking
        q = qty_map.get(key) or qty_map.get((d["job_id"], d["cost_code"]))
        planned = float(q.planned_quantity or 0) if q else 0.0

        # actual_quantity from budget job costs (timecard quantity)
        actual_qty = d["quantity"]

        expected_budget = _compute_expected_budget(
            d["budgeted_all_cost"], planned, actual_qty
        )
        metrics = _calc_cost_metrics(d["budgeted_all_cost"], expected_budget, d["actual_cost"])

        results.append(CostVarianceItem(
            cost_code_id=d["cost_code_id"],
            cost_code=d["cost_code"],
            cost_code_description=d["cost_code_description"],
            job_id=d["job_id"],
            job_name=d["job_name"],
            business_unit=d["business_unit"],
            **metrics,
        ))

    return results


# ── Quantity variance ─────────────────────────────────────────────────────────

def _calc_qty_variance(row: QuantityTrackingResult) -> QuantityVarianceItem:
    planned   = row.planned_quantity   or 0.0
    installed = row.installed_quantity or 0.0

    pct             = (installed / planned * 100) if planned > 0 else 0.0
    remaining       = max(0.0, planned - installed)
    variance_amount = planned - installed

    if pct >= QTY_OVER_THRESHOLD:
        status = "OVER_RISK"
    elif pct >= QTY_NEAR_THRESHOLD:
        status = "NEAR_COMPLETION"
    else:
        status = "ON_TRACK"

    return QuantityVarianceItem(
        cost_code_id=row.cost_code_id,
        cost_code=row.cost_code,
        description=row.description,
        job_id=row.job_id,
        job_code=row.job_code,
        unit=row.unit,
        cost_type=row.cost_type,
        planned_quantity=planned,
        installed_quantity=installed,
        remaining_quantity=remaining,
        qty_variance_amount=variance_amount,
        qty_variance_pct=pct,
        status=status,
        alert=pct >= QTY_NEAR_THRESHOLD,
    )


# ── Public API ────────────────────────────────────────────────────────────────

def get_cost_variance(
    db: Session,
    start_date: str,
    end_date: str,
    *,
    job_id: str | None = None,
    business_unit: str | None = None,
    cost_code: str | None = None,
    status_filter: str | None = None,
):
    dates = _date_list(start_date, end_date)

    budget_rows = (
        db.query(BudgetTrackingResult)
        .filter(BudgetTrackingResult.tracking_date.in_(dates))
        .all()
    )

    # Fetch qty data using the same cache-key logic as the budget module
    cache_key = (
        start_date if start_date == end_date
        else f"{start_date}__{end_date}"
    )
    qty_rows = (
        db.query(QuantityTrackingResult)
        .filter(QuantityTrackingResult.tracking_date == cache_key)
        .all()
    )

    logger.info(
        "Cost variance query → dates=%d budget_rows=%d qty_rows=%d",
        len(dates), len(budget_rows), len(qty_rows),
    )

    items = _aggregate_cost_rows(budget_rows, qty_rows)

    if job_id:
        items = [i for i in items if i.job_id == job_id]
    if business_unit:
        items = [i for i in items if i.business_unit == business_unit]
    if cost_code:
        items = [i for i in items if i.cost_code == cost_code]
    if status_filter:
        items = [i for i in items if i.status == status_filter.upper()]

    return items, VarianceSummary(
        period_start=start_date,
        period_end=end_date,
        total_items=len(items),
        at_risk=sum(1 for i in items if i.status == "OVER_RISK"),
        total_expected_budget=sum(i.expected_budget for i in items),
        total_actual_cost=sum(i.actual_cost for i in items),
        total_cost_variance=sum(i.expected_budget - i.actual_cost for i in items),
    )


def get_quantity_variance(
    db: Session,
    start_date: str,
    end_date: str,
    *,
    job_id: str | None = None,
    cost_type: str | None = None,
    status_filter: str | None = None,
):
    cache_key = (
        start_date if start_date == end_date
        else f"{start_date}__{end_date}"
    )

    rows = (
        db.query(QuantityTrackingResult)
        .filter(QuantityTrackingResult.tracking_date == cache_key)
        .all()
    )

    logger.info(
        "Quantity variance query → cache_key=%s rows=%d",
        cache_key, len(rows),
    )

    items = [
        QuantityVarianceItem(
            cost_code_id=r.cost_code_id,
            cost_code=r.cost_code,
            description=r.description,
            job_id=r.job_id,
            job_code=r.job_code,
            unit=r.unit,
            cost_type=r.cost_type,
            planned_quantity=r.planned_quantity,
            installed_quantity=r.installed_quantity,
            remaining_quantity=max(0.0, r.planned_quantity - r.installed_quantity),
            qty_variance_amount=r.planned_quantity - r.installed_quantity,
            qty_variance_pct=(r.installed_quantity / r.planned_quantity * 100)
            if r.planned_quantity else 0.0,
            status=r.status,
            alert=False,
        )
        for r in rows
    ]

    if job_id:
        items = [i for i in items if i.job_id == job_id]
    if cost_type:
        items = [i for i in items if i.cost_type == cost_type.lower()]
    if status_filter:
        items = [i for i in items if i.status == status_filter.upper()]

    return items, VarianceSummary(
        period_start=start_date,
        period_end=end_date,
        total_items=len(items),
        at_risk=0,
        near_completion=0,
        over_risk=0,
    )