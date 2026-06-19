"""
notifications/projection_email_template.py
────────────────────────────────────────────
HTML email template for monthly projection reports.

Used by:
  - POST /api/projection/notify  (on-demand, manual send)
  - scheduler/monthly_projection_job.py  (auto-send on 23rd of month)

Mirrors budget_email_template.py in structure.

NEW FILE — no changes required to any existing notification files.
"""
from __future__ import annotations

import html
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

_MT = ZoneInfo("America/Denver")


def build_projection_email_body(
    *,
    tracking_month: str,
    items: list,           # list of ProjectionItemRow (Pydantic) or dicts
    comments: str = "",
    triggered_by: str = "Manual",   # "Manual" | "Scheduled" | "Auto-Alert"
) -> tuple[str, str]:
    """
    Build subject + HTML body for a monthly projection report email.

    Args:
        tracking_month: YYYY-MM string
        items:          List of ProjectionItemRow objects (have .status, .alert etc.)
        comments:       Optional user comments appended to the email
        triggered_by:   Source of the send — shown in footer

    Returns:
        (subject, html_body)

    Mirrors build_budget_email_body() in budget_email_template.py.
    """
    on_track_items    = [i for i in items if i.status == "ON_TRACK"]
    at_risk_items     = [i for i in items if i.status == "AT_RISK"]
    over_budget_items = [i for i in items if i.status == "OVER_BUDGET"]
    alert_items       = [i for i in items if i.alert]
    discrepancy_items = [i for i in items if i.discrepancy_flag]

    on_track_count    = len(on_track_items)
    at_risk_count     = len(at_risk_items)
    over_budget_count = len(over_budget_items)
    total_count       = len(items)

    logger.info(
        "Building projection email: month=%s total=%d on_track=%d "
        "at_risk=%d over_budget=%d",
        tracking_month, total_count, on_track_count,
        at_risk_count, over_budget_count,
    )

    # Subject line — escalate wording when there are over-budget jobs
    if over_budget_count > 0:
        subject = (
            f"⚠ Projection Alert — {over_budget_count} Job(s) OVER BUDGET "
            f"| {tracking_month}"
        )
    elif at_risk_count > 0:
        subject = (
            f"Projection Report — {at_risk_count} Job(s) AT RISK "
            f"| {tracking_month}"
        )
    else:
        subject = f"Monthly Projection Report — All Clear | {tracking_month}"

    now_str = datetime.now(_MT).strftime("%Y-%m-%d %H:%M %Z")

    # ── Summary bar ──────────────────────────────────────────────────────────
    summary_bar = (
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        'style="width:100%;max-width:800px;margin:0 0 20px 0;border-collapse:collapse;">'
        "<tr>"
        f'<td style="padding:12px 16px;background:#dcfce7;color:#15803d;font-weight:bold;'
        f'font-family:Arial,Helvetica,sans-serif;font-size:14px;text-align:center;">'
        f"On Track: {on_track_count}</td>"
        f'<td style="padding:12px 16px;background:#ffedd5;color:#c2410c;font-weight:bold;'
        f'font-family:Arial,Helvetica,sans-serif;font-size:14px;text-align:center;">'
        f"At Risk: {at_risk_count}</td>"
        f'<td style="padding:12px 16px;background:#fde8e8;color:#b91c1c;font-weight:bold;'
        f'font-family:Arial,Helvetica,sans-serif;font-size:14px;text-align:center;">'
        f"Over Budget: {over_budget_count}</td>"
        f'<td style="padding:12px 16px;background:#f3f4f6;color:#374151;font-weight:bold;'
        f'font-family:Arial,Helvetica,sans-serif;font-size:14px;text-align:center;">'
        f"Total Jobs: {total_count}</td>"
        "</tr></table>"
    )

    # ── Discrepancy notice ───────────────────────────────────────────────────
    discrepancy_notice = ""
    if discrepancy_items:
        disc_job_list = ", ".join(
            html.escape(i.job_code) for i in discrepancy_items[:10]
        )
        discrepancy_notice = (
            '<div style="margin:0 0 16px 0;padding:12px 16px;'
            'background:#fffbeb;border-left:4px solid #f59e0b;">'
            '<p style="margin:0;font-size:13px;font-weight:bold;color:#92400e;'
            'font-family:Arial,Helvetica,sans-serif;">⚠ Accounting Alert — '
            f'{len(discrepancy_items)} job(s) have projected cost discrepancies '
            f'exceeding 5% vs original budget: {disc_job_list}</p>'
            '</div>'
        )

    # ── Table rows ───────────────────────────────────────────────────────────
    table_rows = _build_table_rows(items)

    # ── Main table ───────────────────────────────────────────────────────────
    projection_table = (
    '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
    'style="width:100%;max-width:1000px;border-collapse:collapse;margin-bottom:16px;">'
    '<thead><tr style="background:#f3f4f6;">'
    + _th("Job Code")
    + _th("Job Name")
    + _th("Budget")
    + _th("Spent To Date")
    + _th("Projected Final")
    + _th("Over / Under")
    + _th("% Used")
    + _th("Status")
    + "</tr></thead><tbody>"
    + "".join(table_rows)
    + "</tbody></table>"
    )

    # ── Comments ─────────────────────────────────────────────────────────────
    comments_section = ""
    if comments and comments.strip():
        comments_section = (
            '<div style="margin:16px 0;padding:12px;background:#f9fafb;'
            'border-left:4px solid #3b82f6;">'
            '<p style="margin:0 0 4px 0;font-weight:bold;font-size:13px;color:#374151;'
            'font-family:Arial,Helvetica,sans-serif;">Comments:</p>'
            f'<p style="margin:0;font-size:13px;color:#1f2937;'
            f'font-family:Arial,Helvetica,sans-serif;">'
            f'{html.escape(comments)}</p>'
            '</div>'
        )

    # ── Footer ───────────────────────────────────────────────────────────────
    footer = (
        '<p style="margin:24px 0 0 0;padding-top:16px;border-top:1px solid #e5e7eb;'
        'font-size:12px;color:#6b7280;font-family:Arial,Helvetica,sans-serif;">'
        f"Sent by Gould Construction APM — {html.escape(now_str)} "
        f"[{html.escape(triggered_by)}]"
        "</p>"
    )

    # ── Assemble ─────────────────────────────────────────────────────────────
    html_body = (
        '<div style="font-family:Arial,Helvetica,sans-serif;color:#111827;'
        'max-width:1000px;">'
        '<h1 style="margin:0 0 8px 0;font-size:20px;color:#111827;">'
        "Monthly Projection Report</h1>"
        f'<p style="margin:0 0 16px 0;font-size:14px;color:#374151;">'
        f"Reporting Month: <strong>{html.escape(tracking_month)}</strong></p>"
        + summary_bar
        + discrepancy_notice
        + projection_table
        + comments_section
        + footer
        + "</div>"
    )

    return subject, html_body


# ── Private helpers ───────────────────────────────────────────────────────────

def _th(label: str) -> str:
    return (
        f'<th style="padding:8px 10px;border:1px solid #e5e7eb;text-align:left;'
        f'font-family:Arial,Helvetica,sans-serif;font-size:12px;color:#374151;">'
        f"{label}</th>"
    )


def _td(content: str, *, align: str = "left", extra_style: str = "") -> str:
    return (
        f'<td style="padding:8px 10px;border:1px solid #e5e7eb;'
        f'font-family:Arial,Helvetica,sans-serif;font-size:13px;'
        f'text-align:{align};{extra_style}">{content}</td>'
    )


def _status_style(status: str) -> tuple[str, str]:
    """Return (bg_color, text_color) for a status badge."""
    return {
        "ON_TRACK":    ("#dcfce7", "#15803d"),
        "AT_RISK":     ("#ffedd5", "#c2410c"),
        "OVER_BUDGET": ("#fde8e8", "#b91c1c"),
    }.get(status, ("#f3f4f6", "#374151"))


def _fmt_currency(value: float) -> str:
    return f"${value:,.2f}"


def _fmt_variance(value: float) -> str:
    """Positive variance = under budget (green), negative = over budget (red)."""
    if value >= 0:
        return f'<span style="color:#15803d;">${value:,.2f}</span>'
    return f'<span style="color:#b91c1c;">-${abs(value):,.2f}</span>'


def _build_table_rows(items: list) -> list[str]:
    rows = []
    for item in items:
        # Skip internal overhead jobs with no budget
        if item.original_budget == 0:
            continue

        bg, fg = _status_style(item.status)

        status_badge = (
            f'<span style="display:inline-block;padding:3px 8px;border-radius:4px;'
            f'background:{bg};color:{fg};font-weight:bold;font-size:11px;">'
            f"{html.escape(item.status)}</span>"
        )

        # Over/Under — green if positive (under budget), red if negative (over budget)
        if item.cost_variance >= 0:
            variance_display = (
                f'<span style="color:#15803d;font-weight:bold;">'
                f'+${item.cost_variance:,.0f}</span>'
            )
        else:
            variance_display = (
                f'<span style="color:#b91c1c;font-weight:bold;">'
                f'-${abs(item.cost_variance):,.0f}</span>'
            )

        # % Used — red if over 100%, orange if over 85%, green otherwise
        pct = item.percent_complete
        if pct >= 100:
            pct_color = "#b91c1c"
        elif pct >= 85:
            pct_color = "#c2410c"
        else:
            pct_color = "#15803d"

        pct_display = (
            f'<span style="color:{pct_color};font-weight:bold;">'
            f'{pct:.1f}%</span>'
        )

        row = (
            "<tr>"
            + _td(html.escape(item.job_code), extra_style="font-weight:bold;")
            + _td(html.escape(item.job_name))
            + _td(f"${item.original_budget:,.0f}", align="right")
            + _td(f"${item.actual_cost_to_date:,.0f}", align="right")
            + _td(f"${item.projected_final_cost:,.0f}", align="right")
            + _td(variance_display, align="right")
            + _td(pct_display, align="right")
            + _td(status_badge, align="center")
            + "</tr>"
        )
        rows.append(row)
    return rows