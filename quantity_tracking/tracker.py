"""
Quantity Tracking — core engine.

DESIGN:
  Uses timecards as a bridge to find active job IDs for a given date/range.
  Only jobs with actual timecard activity in the period are tracked —
  avoiding rate limiting from querying all jobs.

Flow:
  1. Fetch timecards for date/range → extract unique job IDs
  2. Resolve real job codes from /jobs/advanced
  3. Batch fetch cost codes for those job IDs
  4. Fetch cost-code progress per active job for the SAME selected date range
  5. Sum progress["quantity"] per cost code to match HCSS Cost Code Summary
  6. Join and calculate percent complete + status

Installed quantity rule:
  HCSS Cost Code Summary Actual Quantity changes with the selected report range.
  Therefore installed_quantity must be:

      SUM(costCode/progress/advancedRequest["quantity"])
      for resolved_start → resolved_end

  Do NOT use quantityToDate / toDateQuantity / totalQuantity for this report value.
  Those are cumulative/debug fields and can cause mismatches or double counting.

Status rules:
  ON_TRACK        — < 75% complete
  NEAR_COMPLETION — >= 75% and < 100%
  OVER_RISK       — >= 100%
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

import requests

from payroll_verification.hcss_client import HCSSClient

logger = logging.getLogger(__name__)

Status = Literal["ON_TRACK", "NEAR_COMPLETION", "OVER_RISK"]

ALERT_THRESHOLD = 75.0
_COST_CODES_URL = "https://api.hcssapps.com/heavyjob/api/v2/costCodes/advancedRequest"


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class QuantityResult:
    cost_code_id: str
    cost_code: str
    description: str
    job_id: str
    job_code: str
    unit: str
    cost_type: str           # "self_perform" | "subcontractor"
    planned_quantity: float
    installed_quantity: float
    percent_complete: float
    status: Status
    alert: bool

    @property
    def remaining_quantity(self) -> float:
        return max(0.0, self.planned_quantity - self.installed_quantity)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_float(val: Any) -> float:
    try:
        return float(val or 0)
    except (TypeError, ValueError):
        return 0.0


def _determine_cost_type(cost_code: dict[str, Any]) -> str:
    raw = (
        cost_code.get("costType")
        or cost_code.get("type")
        or cost_code.get("workType")
        or ""
    ).lower()
    return "subcontractor" if "sub" in raw else "self_perform"


def _calculate_status(percent: float) -> Status:
    if percent >= 100.0:
        return "OVER_RISK"
    if percent >= ALERT_THRESHOLD:
        return "NEAR_COMPLETION"
    return "ON_TRACK"


def _resolve_dates(
    target_date: str | None,
    start_date: str | None,
    end_date: str | None,
) -> tuple[str, str]:
    """
    Resolve date inputs to (start, end) pair.
    Priority: target_date > start_date+end_date > current month default.
    """
    if target_date:
        return target_date, target_date
    if start_date and end_date:
        return start_date, end_date
    today = date.today()
    return today.replace(day=1).isoformat(), today.isoformat()


def _extract_results_and_cursor(data: Any) -> tuple[list[dict[str, Any]], str | None]:
    """
    Extract results and next cursor from HCSS paginated response.
    """
    if not isinstance(data, dict):
        return data or [], None  # type: ignore[return-value]

    results = data.get("results", []) or []
    metadata = data.get("metadata") or {}
    next_cursor = metadata.get("nextCursor")

    return results, next_cursor


def _fetch_all_pages(
    client: HCSSClient,
    method: str,
    url: str,
    payload: dict[str, Any],
    *,
    label: str,
    page_delay: float = 0.75,
    max_retries: int = 8,
) -> list[dict[str, Any]]:
    """
    Fetch all cursor-paginated HCSS rows.

    This prevents silent truncation when cost codes or progress rows exceed
    the first page limit. Also handles 429 rate-limit responses with backoff.
    """
    all_results: list[dict[str, Any]] = []
    cursor: str | None = None
    page = 0

    while True:
        req_payload = dict(payload)

        if cursor:
            req_payload["cursor"] = cursor

        retries = 0

        while True:
            try:
                if method.upper() == "POST":
                    raw = client._request(method, url, json=req_payload)
                else:
                    raw = client._request(method, url, params=req_payload)
                break

            except requests.exceptions.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else None

                if status == 429 and retries < max_retries:
                    retry_after = None

                    if exc.response is not None:
                        retry_after = exc.response.headers.get("Retry-After")

                    wait_seconds = (
                        float(retry_after)
                        if retry_after
                        else min(60.0, (2 ** retries) * 5.0)
                    )

                    retries += 1

                    logger.warning(
                        "HCSS 429 rate limit for %s page=%d; waiting %.1fs "
                        "retry=%d/%d",
                        label,
                        page + 1,
                        wait_seconds,
                        retries,
                        max_retries,
                    )

                    time.sleep(wait_seconds)
                    continue

                raise

        results, next_cursor = _extract_results_and_cursor(raw)

        page += 1
        all_results.extend(results)

        logger.info(
            "HCSS page fetched: %s page=%d rows=%d total=%d",
            label,
            page,
            len(results),
            len(all_results),
        )

        if not next_cursor:
            break

        cursor = next_cursor
        time.sleep(page_delay)

    return all_results


def _resolve_job_codes(
    client: HCSSClient,
    job_ids: list[str],
    fallback_map: dict[str, str],
) -> dict[str, str]:
    """
    Resolve job_id → real job code from /jobs/advanced.

    timeCardInfo does not always return jobCode, so relying only on timecards
    can store "Unknown". The working test script used /jobs/advanced, so this
    helper brings the same behavior into the real tracker.
    """
    job_map = dict(fallback_map)

    if not job_ids:
        return job_map

    try:
        raw = client._request(
            "POST",
            f"{client.heavyjob_base}/jobs/advanced",
            json={"jobIds": job_ids},
        )

        job_list = raw if isinstance(raw, list) else raw.get("results", [])

    except Exception as exc:
        logger.warning("Could not resolve job codes from /jobs/advanced: %s", exc)
        return job_map

    resolved = 0

    for job in job_list:
        jid = job.get("id") or job.get("jobId")

        if not jid:
            continue

        code = (
            job.get("code")
            or job.get("jobCode")
            or job.get("number")
            or job.get("jobNumber")
        )

        if code:
            job_map[jid] = str(code)
            resolved += 1

    logger.info("Resolved %d job code(s) from /jobs/advanced", resolved)

    return job_map


# ---------------------------------------------------------------------------
# Main tracking function
# ---------------------------------------------------------------------------

def track_quantities(
    *,
    target_date: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    client: HCSSClient | None = None,
) -> list[QuantityResult]:
    """
    Track quantities for all jobs active in the given date or date range.

    Uses timecards to discover which jobs had activity in the period, then
    fetches cost codes and progress only for those jobs.

    Args:
        target_date: Single date YYYY-MM-DD (sets both start and end).
        start_date:  Range start YYYY-MM-DD.
        end_date:    Range end YYYY-MM-DD.
        client:      Optional HCSSClient instance.
    """
    c = client or HCSSClient()

    resolved_start, resolved_end = _resolve_dates(target_date, start_date, end_date)

    logger.info(
        "Starting quantity tracking: period=%s to %s",
        resolved_start,
        resolved_end,
    )

    # ------------------------------------------------------------------ #
    # Step 1: Fetch timecards for the period → extract unique active job IDs
    # ------------------------------------------------------------------ #
    try:
        timecard_summaries = c.fetch_timecards(
            start_date=resolved_start,
            end_date=resolved_end,
        )

    except Exception as exc:
        logger.error("Failed to fetch timecards for period: %s", exc)
        raise RuntimeError(f"Could not fetch timecards from HCSS: {exc}") from exc

    if not timecard_summaries:
        logger.info(
            "No timecards found for period=%s to %s — no active jobs to track",
            resolved_start,
            resolved_end,
        )
        return []

    # Extract unique job IDs and any available job-code hints from timecards.
    job_map: dict[str, str] = {}   # job_id → job_code

    for tc in timecard_summaries:
        jid = tc.get("jobId") or tc.get("job_id")

        if not jid:
            continue

        jcode = (
            tc.get("jobCode")
            or tc.get("job_code")
            or tc.get("jobNumber")
            or tc.get("jobDescription")
            or "Unknown"
        )

        if jid not in job_map:
            job_map[jid] = str(jcode)

    active_job_ids = list(job_map.keys())

    logger.info(
        "Found %d timecard(s) → %d unique active job(s) for period=%s to %s",
        len(timecard_summaries),
        len(active_job_ids),
        resolved_start,
        resolved_end,
    )

    if not active_job_ids:
        logger.warning("No job IDs found in timecards for this period")
        return []

    # Important:
    # timeCardInfo may not include real jobCode. Resolve the same way
    # as the working test script did.
    job_map = _resolve_job_codes(c, active_job_ids, job_map)

    # ------------------------------------------------------------------ #
    # Step 2: Batch fetch cost codes for all active jobs
    # ------------------------------------------------------------------ #
    try:
        all_cost_codes = _fetch_all_pages(
            c,
            "POST",
            _COST_CODES_URL,
            {"jobIds": active_job_ids, "limit": 500},
            label="quantity cost codes",
            page_delay=0.75,
        )

    except Exception as exc:
        logger.error("Failed to fetch cost codes for active jobs: %s", exc)
        raise RuntimeError(f"Could not fetch cost codes from HCSS: {exc}") from exc

    if not all_cost_codes:
        logger.warning(
            "No cost codes found for %d active job(s)",
            len(active_job_ids),
        )
        return []

    logger.info(
        "Fetched %d cost code(s) across %d active job(s)",
        len(all_cost_codes),
        len(active_job_ids),
    )

    # Build lookup: cost_code_id → cost_code dict + cost_code_id → job_id.
    cc_map: dict[str, dict[str, Any]] = {}
    cc_job_map: dict[str, str] = {}

    for cc in all_cost_codes:
        ccid = cc.get("id") or cc.get("costCodeId")
        jid = cc.get("jobId") or cc.get("job_id")

        if ccid:
            cc_map[ccid] = cc

            if jid:
                cc_job_map[ccid] = jid

                # Some cost-code rows may contain job code fields.
                # Use them only as fallback if /jobs/advanced did not help.
                cc_job_code = (
                    cc.get("jobCode")
                    or cc.get("job_code")
                    or cc.get("jobNumber")
                )

                if cc_job_code and job_map.get(jid, "Unknown") == "Unknown":
                    job_map[jid] = str(cc_job_code)

    # ------------------------------------------------------------------ #
    # Step 3: Fetch quantity progress per active job for the selected range
    # ------------------------------------------------------------------ #
    installed_map: dict[str, float] = {}   # cost_code_id → actual/installed qty

    for jid in active_job_ids:
        try:
            progress_list = _fetch_all_pages(
                c,
                "POST",
                f"{c.heavyjob_base}/costCode/progress/advancedRequest",
                {
                    "jobId": jid,
                    "startDate": f"{resolved_start}T00:00:00Z",
                    "endDate": f"{resolved_end}T23:59:59Z",
                    "limit": 500,
                },
                label=f"quantity progress job={jid[:8]}",
                page_delay=1.0,
            )

            logger.info(
                "Progress: job=%s fetched %d record(s)",
                jid,
                len(progress_list),
            )

            for p in progress_list:
                ccid = p.get("costCodeId") or p.get("id")

                if not ccid:
                    continue

                # IMPORTANT:
                # HCSS Cost Code Summary Actual Quantity is date-range based.
                # Use only the daily/period production field "quantity" and
                # sum it over the selected date range.
                #
                # Do not use cumulative fields such as:
                # - toDateQuantity
                # - quantityToDate
                # - totalQuantity
                qty = _safe_float(p.get("quantity"))

                installed_map[ccid] = installed_map.get(ccid, 0.0) + qty

        except Exception as exc:
            logger.error(
                "Failed to fetch progress for job=%s: %s — "
                "installed quantities for this job will show as 0",
                jid,
                exc,
            )
            continue

    # ------------------------------------------------------------------ #
    # Step 4: Join cost codes + progress → calculate status
    # ------------------------------------------------------------------ #
    results: list[QuantityResult] = []

    for ccid, cc in cc_map.items():
        planned = _safe_float(
            cc.get("quantity")
            or cc.get("plannedQuantity")
            or cc.get("budgetQuantity")
            or cc.get("budgetedQuantity")
        )

        if planned <= 0:
            continue

        installed = installed_map.get(ccid, 0.0)
        percent = round((installed / planned) * 100, 2)
        st = _calculate_status(percent)
        jid = cc_job_map.get(ccid, "")

        results.append(
            QuantityResult(
                cost_code_id=ccid,
                cost_code=(
                    cc.get("code")
                    or cc.get("costCode")
                    or cc.get("number")
                    or ccid[:8]
                ),
                description=(
                    cc.get("description")
                    or cc.get("name")
                    or cc.get("costCodeDescription")
                    or "No description"
                ),
                job_id=jid,
                job_code=job_map.get(jid, "Unknown"),
                unit=(
                    cc.get("unit")
                    or cc.get("unitOfMeasure")
                    or cc.get("uom")
                    or "UNIT"
                ),
                cost_type=_determine_cost_type(cc),
                planned_quantity=planned,
                installed_quantity=round(installed, 4),
                percent_complete=percent,
                status=st,
                alert=percent >= ALERT_THRESHOLD,
            )
        )

    on_track = sum(1 for r in results if r.status == "ON_TRACK")
    near = sum(1 for r in results if r.status == "NEAR_COMPLETION")
    over = sum(1 for r in results if r.status == "OVER_RISK")

    logger.info(
        "Quantity tracking done: period=%s to %s active_jobs=%d "
        "total=%d on_track=%d near=%d over=%d",
        resolved_start,
        resolved_end,
        len(active_job_ids),
        len(results),
        on_track,
        near,
        over,
    )

    return results