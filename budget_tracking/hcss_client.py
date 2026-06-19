"""
HCSS API wrapper for budget-specific API calls.

Fetch strategy (5 API calls, date-filtered):

  Call 1 — GET /v1/timeCardInfo  (startDate, endDate)
            Discover which job IDs had timecard activity in the date window.
            Used to scope Calls 2–5 to only active jobs.

  Call 2 — GET /v1/costCodes  (per job, paginated)
            Returns planned budget per cost code including ALL budget types:
              laborDollars, equipmentDollars, materialDollars,
              subcontractDollars, supplyDollars, customCostTypeDollars
            supplyDollars is ONLY available on v1 (not v2) — critical for
            correct budget totals matching the HCSS portal.

  Call 3 — POST /v1/jobCosts/advancedRequest  (startDate, endDate, jobIds)
            Returns date-filtered labor + equipment costs from timecards.
            Flat response — costCodeId is a top-level field.
            One row per (costCodeId, foremanId, date) — aggregated by costCodeId.

  Call 4 — POST /v1/costTypes/materialInstalled/advancedRequest
            Returns date-filtered material costs posted via purchase orders or
            directly — NOT captured in v1/jobCosts.
            Merged into actual_map by (jobId, costCodeId).

  Call 5 — POST /v1/costTypes/subcontractWork/advancedRequest
            Returns date-filtered subcontract costs posted independently —
            NOT captured in v1/jobCosts when not tied to a timecard.
            Merged into actual_map by (jobId, costCodeId).

  Combine — JOIN Call 2 + Calls 3+4+5 on costCodeId.
            actual_cost = labor + equipment + material + subcontract + trucking
            expected_budget = labor + equipment + material + subcontract + supply + custom

WHY THIS APPROACH:
  - Date-filtered: costs are only for the requested window, matching portal behavior
  - All cost types: v1/jobCosts alone misses PO-posted material and standalone
    subcontract costs. Calls 4+5 capture those, matching HCSS portal exactly.
  - Correct budget: v1/costCodes includes supplyDollars which v2 omits
  - Verified: actual_cost and expected_budget match HCSS portal exactly
"""
from __future__ import annotations

import logging
from typing import Any

from payroll_verification.hcss_client import HCSSClient

logger = logging.getLogger(__name__)

_TIMECARDS_URL  = "https://api.hcssapps.com/heavyjob/api/v1/timeCardInfo"
_COST_CODES_V1  = "https://api.hcssapps.com/heavyjob/api/v1/costCodes"
_JOB_COSTS_V1   = "https://api.hcssapps.com/heavyjob/api/v1/jobCosts/advancedRequest"
_MATERIAL_URL   = "https://api.hcssapps.com/heavyjob/api/v1/costTypes/materialInstalled/advancedRequest"
_SUBCONTRACT_URL = "https://api.hcssapps.com/heavyjob/api/v1/costTypes/subcontractWork/advancedRequest"


class BudgetHCSSClient:
    """Wrapper for HCSS budget-specific API calls."""

    def __init__(self, client: HCSSClient | None = None):
        self.client = client or HCSSClient()
        logger.debug("BudgetHCSSClient initialized")

    # ──────────────────────────────────────────────────────────────────────────
    # Private helpers
    # ──────────────────────────────────────────────────────────────────────────

    def _paginate_get(self, url: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        """Paginate a GET endpoint using cursor."""
        rows: list[dict[str, Any]] = []
        cursor: str | None = None
        page = 0
        while True:
            if cursor:
                params = dict(params)
                params["cursor"] = cursor
            raw = self.client._request("GET", url, params=params)
            batch, next_cursor = self.client._extract_results_and_cursor(raw)
            page += 1
            rows.extend(batch)
            if not next_cursor:
                break
            cursor = next_cursor
        return rows

    def _paginate_post(self, url: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
        """Paginate a POST endpoint using cursor."""
        rows: list[dict[str, Any]] = []
        cursor: str | None = None
        page = 0
        while True:
            if cursor:
                payload = dict(payload)
                payload["cursor"] = cursor
            raw = self.client._request("POST", url, json=payload)
            batch, next_cursor = self.client._extract_results_and_cursor(raw)
            page += 1
            rows.extend(batch)
            if not next_cursor:
                break
            cursor = next_cursor
        return rows

    def _fetch_active_job_ids(
        self,
        start_date: str,
        end_date: str,
        business_unit_id: str | None = None,
    ) -> list[str]:
        """
        Call 1: GET /v1/timeCardInfo
        Returns job UUIDs that had timecard activity in the date window.
        """
        params: dict[str, Any] = {"startDate": start_date, "endDate": end_date}
        if business_unit_id:
            params["businessUnitId"] = business_unit_id

        rows = self._paginate_get(_TIMECARDS_URL, params)
        job_ids: dict[str, bool] = {}
        for tc in rows:
            jid = tc.get("jobId") or tc.get("job_id") or ""
            if jid:
                job_ids[jid] = True

        logger.info(
            "timeCardInfo: %d timecards → %d active jobs for %s→%s",
            len(rows), len(job_ids), start_date, end_date,
        )
        return list(job_ids.keys())

    def _fetch_cost_code_budgets(
        self,
        job_ids: list[str],
    ) -> dict[str, dict[str, Any]]:
        """
        Call 2: GET /v1/costCodes  (per job)
        Returns { costCodeId: record } with full planned budget breakdown.

        Uses v1 (not v2) because v1 includes supplyDollars which is required
        for correct budget totals matching the portal.

        expected_budget = labor + equipment + material + subcontract + supply + custom
        """
        budget_map: dict[str, dict[str, Any]] = {}
        for jid in job_ids:
            rows = self._paginate_get(_COST_CODES_V1, {"jobId": jid, "limit": 500})
            for cc in rows:
                ccid = cc.get("id")
                if ccid:
                    budget_map[ccid] = cc

        logger.info(
            "costCodes v1: %d cost codes for %d job(s)",
            len(budget_map), len(job_ids),
        )
        return budget_map

    def _fetch_actual_costs(
        self,
        job_ids: list[str],
        start_date: str,
        end_date: str,
    ) -> dict[str, dict[str, Any]]:
        """
        Call 3: POST /v1/jobCosts/advancedRequest  (date-filtered)
        Returns flat rows: { costCodeId, laborCost, equipmentCost,
                              materialCost, subcontractCost, truckingCost, laborHours, ... }
        One row per (costCodeId, foremanId, date) — aggregated by costCodeId.

        NOTE: This captures labor + equipment well, but materialCost and
        subcontractCost here only reflect timecard-linked entries. Standalone
        PO material costs and independently-posted subcontract costs are
        captured separately via _fetch_material_costs() and
        _fetch_subcontract_costs().
        """
        rows = self._paginate_post(_JOB_COSTS_V1, {
            "jobIds":    job_ids,
            "startDate": start_date,
            "endDate":   end_date,
            "limit":     500,
        })

        agg: dict[str, dict[str, Any]] = {}
        for row in rows:
            ccid = row.get("costCodeId") or ""
            if not ccid:
                continue

            # v1/jobCosts flat response — non-prefixed field names
            labor = float(row.get("laborCost")       or 0)
            equip = float(row.get("equipmentCost")   or 0)
            mat   = float(row.get("materialCost")    or 0)
            sub   = float(row.get("subcontractCost") or 0)
            truck = float(row.get("truckingCost")    or 0)
            hours = float(row.get("laborHours")      or 0)
            qty   = float(row.get("quantity")        or 0)

            if ccid in agg:
                agg[ccid]["laborCost"]       += labor
                agg[ccid]["equipmentCost"]   += equip
                agg[ccid]["materialCost"]    += mat
                agg[ccid]["subcontractCost"] += sub
                agg[ccid]["truckingCost"]    += truck
                agg[ccid]["laborHours"]      += hours
                agg[ccid]["quantity"]        += qty
                agg[ccid]["actualCost"]      += labor + equip + mat + sub + truck
            else:
                agg[ccid] = {
                    "actualCost":      labor + equip + mat + sub + truck,
                    "laborCost":       labor,
                    "equipmentCost":   equip,
                    "materialCost":    mat,
                    "subcontractCost": sub,
                    "truckingCost":    truck,
                    "laborHours":      hours,
                    "quantity":        qty,
                }

        nonzero = sum(1 for v in agg.values() if v["actualCost"] > 0)
        logger.info(
            "jobCosts v1: %d rows → %d cost codes (%d with actual cost > 0) for %s→%s",
            len(rows), len(agg), nonzero, start_date, end_date,
        )
        return agg

    def _fetch_material_costs(
        self,
        job_ids: list[str],
        start_date: str,
        end_date: str,
        agg: dict[str, dict[str, Any]],
    ) -> None:
        """
        Call 4: POST /v1/costTypes/materialInstalled/advancedRequest
        Fetches material costs posted via purchase orders or directly —
        these are NOT returned by v1/jobCosts when not linked to a timecard.
        Merges into the existing agg dict in-place keyed by costCodeId.
        """
        rows = self._paginate_post(_MATERIAL_URL, {
            "jobIds":    job_ids,
            "startDate": start_date,
            "endDate":   end_date,
            "limit":     500,
        })

        added = 0
        for row in rows:
            cc_info  = row.get("costCode") or row.get("costcode") or {}
            job_info = row.get("job") or {}

            ccid = (cc_info.get("costCodeId") or cc_info.get("id")
                    or row.get("costCodeId") or "")
            if not ccid:
                continue

            mat_cost = float(
                row.get("totalCost")
                or row.get("cost")
                or row.get("materialCost")
                or row.get("totalMaterialCost")
                or row.get("extendedCost")
                or (float(row.get("unitCost") or 0)
                    * float(row.get("quantity") or row.get("installedQuantity") or 0))
                or 0
            )

            if ccid in agg:
                agg[ccid]["materialCost"] += mat_cost
                agg[ccid]["actualCost"]   += mat_cost
            else:
                agg[ccid] = {
                    "actualCost":      mat_cost,
                    "laborCost":       0.0,
                    "equipmentCost":   0.0,
                    "materialCost":    mat_cost,
                    "subcontractCost": 0.0,
                    "truckingCost":    0.0,
                    "laborHours":      0.0,
                    "quantity":        0.0,
                }
                added += 1

        mat_nonzero = sum(1 for v in agg.values() if v["materialCost"] > 0)
        logger.info(
            "materialInstalled: %d rows → %d cost codes with material costs "
            "(%d new entries) for %s→%s",
            len(rows), mat_nonzero, added, start_date, end_date,
        )

    def _fetch_subcontract_costs(
        self,
        job_ids: list[str],
        start_date: str,
        end_date: str,
        agg: dict[str, dict[str, Any]],
    ) -> None:
        """
        Call 5: POST /v1/costTypes/subcontractWork/advancedRequest
        Fetches subcontract costs posted independently of timecards —
        these are NOT returned by v1/jobCosts when not linked to a timecard.
        Merges into the existing agg dict in-place keyed by costCodeId.
        """
        rows = self._paginate_post(_SUBCONTRACT_URL, {
            "jobIds":    job_ids,
            "startDate": start_date,
            "endDate":   end_date,
            "limit":     500,
        })

        added = 0
        for row in rows:
            cc_info  = row.get("costCode") or row.get("costcode") or {}

            ccid = (cc_info.get("costCodeId") or cc_info.get("id")
                    or row.get("costCodeId") or "")
            if not ccid:
                continue

            sub_cost = float(
                row.get("totalCost")
                or row.get("cost")
                or row.get("subcontractCost")
                or row.get("totalSubcontractCost")
                or row.get("extendedCost")
                or 0
            )

            if ccid in agg:
                agg[ccid]["subcontractCost"] += sub_cost
                agg[ccid]["actualCost"]      += sub_cost
            else:
                agg[ccid] = {
                    "actualCost":      sub_cost,
                    "laborCost":       0.0,
                    "equipmentCost":   0.0,
                    "materialCost":    0.0,
                    "subcontractCost": sub_cost,
                    "truckingCost":    0.0,
                    "laborHours":      0.0,
                    "quantity":        0.0,
                }
                added += 1

        sub_nonzero = sum(1 for v in agg.values() if v["subcontractCost"] > 0)
        logger.info(
            "subcontractWork: %d rows → %d cost codes with subcontract costs "
            "(%d new entries) for %s→%s",
            len(rows), sub_nonzero, added, start_date, end_date,
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Public interface
    # ──────────────────────────────────────────────────────────────────────────

    def fetch_budget_summary(
        self,
        *,
        date: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        job_id: str | None = None,
        business_unit: str | None = None,
        cost_code: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Fetch cost code budget summary from HCSS for a date or date range.

        Returns date-filtered actual costs joined with planned budget per cost code.
        Only cost codes that had any activity (any cost type) in the window are returned.

        Args:
            date:          Single date YYYY-MM-DD (used as both start and end)
            start_date:    Range start YYYY-MM-DD
            end_date:      Range end YYYY-MM-DD
            job_id:        Optional — skip timecard discovery, use this job only
            business_unit: Optional — filter timecards by business unit
            cost_code:     Optional — filter results by cost code string post-fetch

        Returns:
            List of dicts with:
              job { jobId, jobCode, jobDescription }
              costCode { costCodeId, costCodeCode, costCodeDescription }
              actualCost, laborCost, equipmentCost, materialCost,
              subcontractCost, truckingCost, laborHours, quantity
              expectedBudget, laborBudget, equipmentBudget, materialBudget,
              subcontractBudget, supplyBudget, businessUnit

        Raises:
            ValueError:   If no date params provided.
            RuntimeError: If an HCSS API call fails.
        """
        # ── Resolve date range ────────────────────────────────────────────────
        if date:
            query_start = date
            query_end   = date
        elif start_date and end_date:
            query_start = start_date
            query_end   = end_date
        else:
            raise ValueError("Either date or (start_date and end_date) must be provided")

        logger.info(
            "fetch_budget_summary: %s→%s job=%s bu=%s",
            query_start, query_end, job_id or "all", business_unit or "all",
        )

        # ── Call 1: active job IDs ────────────────────────────────────────────
        if job_id:
            active_job_ids = [job_id]
        else:
            try:
                active_job_ids = self._fetch_active_job_ids(
                    query_start, query_end, business_unit,
                )
            except Exception as exc:
                raise RuntimeError(f"Failed to fetch active jobs: {exc}") from exc

        if not active_job_ids:
            logger.warning("No active jobs in %s→%s", query_start, query_end)
            return []

        # ── Call 2: planned budgets (v1 — includes supplyDollars) ────────────
        try:
            budget_map = self._fetch_cost_code_budgets(active_job_ids)
        except Exception as exc:
            raise RuntimeError(f"Failed to fetch cost code budgets: {exc}") from exc

        if not budget_map:
            logger.warning("No cost codes found for %d jobs", len(active_job_ids))
            return []

        # ── Call 3: date-filtered actual costs from timecards (v1 flat) ─────────
        try:
            actual_map = self._fetch_actual_costs(active_job_ids, query_start, query_end)
        except Exception as exc:
            raise RuntimeError(f"Failed to fetch actual costs: {exc}") from exc

        # ── Call 4: material costs posted via POs or directly ─────────────────
        # These are NOT in v1/jobCosts when not linked to a timecard.
        try:
            self._fetch_material_costs(active_job_ids, query_start, query_end, actual_map)
        except Exception as exc:
            logger.warning(
                "Failed to fetch material costs for %s→%s: %s — continuing without",
                query_start, query_end, exc,
            )

        # ── Call 5: subcontract costs posted independently ────────────────────
        # These are NOT in v1/jobCosts when not tied to a timecard.
        try:
            self._fetch_subcontract_costs(active_job_ids, query_start, query_end, actual_map)
        except Exception as exc:
            logger.warning(
                "Failed to fetch subcontract costs for %s→%s: %s — continuing without",
                query_start, query_end, exc,
            )

        # ── Combine: only cost codes with actual cost > 0 ────────────────────
        # (Same as original logic — only active cost codes returned)
        combined: list[dict[str, Any]] = []

        for ccid, act in actual_map.items():
            if act["actualCost"] <= 0:
                continue  # skip zero-cost codes — same as original

            cc = budget_map.get(ccid, {})

            # Budget breakdown — v1 includes supplyDollars
            labor_bud  = float(cc.get("laborDollars")       or 0)
            equip_bud  = float(cc.get("equipmentDollars")   or 0)
            mat_bud    = float(cc.get("materialDollars")    or 0)
            sub_bud    = float(cc.get("subcontractDollars") or 0)
            supply_bud = float(cc.get("supplyDollars")      or 0)
            # customCostTypeDollars is an array — sum if present
            custom_arr = cc.get("customCostTypeDollars") or []
            custom_bud = sum(
                float(c.get("dollars") or c.get("amount") or 0) for c in custom_arr
            )
            total_bud = labor_bud + equip_bud + mat_bud + sub_bud + supply_bud + custom_bud

            # Job + cost code metadata from budget record (budget_map has job context)
            cc_job_id   = cc.get("jobId", "")
            cc_job_code = cc.get("jobCode", "")
            cc_job_desc = cc.get("jobDescription") or ""
            bu_code     = cc.get("businessUnitCode") or cc.get("businessUnitId") or "N/A"

            combined.append({
                "job": {
                    "jobId":          cc_job_id,
                    "jobCode":        cc_job_code,
                    "jobDescription": cc_job_desc,
                },
                "costCode": {
                    "costCodeId":          ccid,
                    "costCodeCode":        cc.get("code") or "",
                    "costCodeDescription": cc.get("description") or "",
                },
                "actualCost":      act["actualCost"],
                "laborCost":       act["laborCost"],
                "equipmentCost":   act["equipmentCost"],
                "materialCost":    act["materialCost"],
                "subcontractCost": act["subcontractCost"],
                "truckingCost":    act["truckingCost"],
                "laborHours":      act["laborHours"],
                "quantity":        act["quantity"],
                "foremen":         [],  # v1/jobCosts flat — no foreman detail
                "expectedBudget":  total_bud,
                "laborBudget":     labor_bud,
                "equipmentBudget": equip_bud,
                "materialBudget":  mat_bud,
                "subcontractBudget": sub_bud,
                "supplyBudget":    supply_bud,
                "businessUnit":    bu_code,
            })

        logger.info(
            "fetch_budget_summary done: %d cost codes with cost for %s→%s",
            len(combined), query_start, query_end,
        )

        # ── Optional cost_code filter ─────────────────────────────────────────
        if cost_code:
            combined = [
                r for r in combined
                if r["costCode"].get("costCodeCode", "") == cost_code
            ]

        return combined
