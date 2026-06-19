from __future__ import annotations

import csv
import io
from dataclasses import asdict
from typing import Any

from payroll_verification.verifier import TimecardResult


def group_results(results: list[TimecardResult]) -> dict[str, list[TimecardResult]]:
    grouped: dict[str, list[TimecardResult]] = {"REJECTED": [], "FLAGGED": [], "APPROVED": []}
    for r in results:
        grouped[r.status].append(r)
    return grouped


def results_to_rows(results: list[TimecardResult]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for r in results:
        d = asdict(r)
        d["why"] = r.why
        # Keep reasons and flags as lists — TimecardRow schema expects list[str]
        rows.append(d)
    return rows


def results_to_csv_bytes(results: list[TimecardResult]) -> bytes:
    rows = results_to_rows(results)
    out = io.StringIO()
    # Use a fixed column order derived from the dataclass so an empty result
    # still produces a valid CSV with headers.
    from dataclasses import fields as _fields
    fieldnames = [f.name for f in _fields(TimecardResult)] + ["why"]
    seen: set[str] = set()
    ordered: list[str] = []
    for name in fieldnames:
        if name not in seen:
            seen.add(name)
            ordered.append(name)
    writer = csv.DictWriter(out, fieldnames=ordered)
    writer.writeheader()
    for row in rows:
        # Flatten list fields to semicolon-separated strings for CSV
        csv_row = dict(row)
        csv_row["reasons"] = "; ".join(row["reasons"]) if row["reasons"] else ""
        csv_row["flags"] = "; ".join(row["flags"]) if row["flags"] else ""
        writer.writerow(csv_row)
    return out.getvalue().encode("utf-8")
