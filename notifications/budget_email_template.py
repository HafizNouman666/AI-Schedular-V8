"""
Email template for budget tracking reports.
"""
from __future__ import annotations

import html
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

_MT = ZoneInfo("America/Denver")


def build_budget_email_body(
    *,
    period_start: str,
    period_end: str,
    items: list,
    comments: str = "",
) -> tuple[str, str]:
    """
    Build subject + HTML body for budget report email.
    
    Args:
        period_start: Start date YYYY-MM-DD
        period_end: End date YYYY-MM-DD
        items: List of BudgetItemRow objects
        comments: Optional user comments
        
    Returns:
        (subject, html_body)
    """
    on_track = [item for item in items if item.status == "ON_TRACK"]
    over_risk = [item for item in items if item.status == "OVER_RISK"]
    on_track_count = len(on_track)
    over_risk_count = len(over_risk)
    total_count = len(items)
    
    logger.info(
        "Building budget email: period=%s to %s total=%d on_track=%d over_risk=%d",
        period_start,
        period_end,
        total_count,
        on_track_count,
        over_risk_count,
    )
    
    # Subject
    subject = "Budget Tracking Report — Cost Code Summary"
    
    # Current timestamp
    now_str = datetime.now(_MT).strftime("%Y-%m-%d %H:%M %Z")
    
    # Summary bar
    summary_bar = (
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        'style="width:100%;max-width:640px;margin:0 0 20px 0;border-collapse:collapse;">'
        "<tr>"
        f'<td style="padding:12px 16px;background:#dcfce7;color:#15803d;font-weight:bold;'
        f'font-family:Arial,Helvetica,sans-serif;font-size:14px;text-align:center;">'
        f"On Track: {on_track_count}</td>"
        f'<td style="padding:12px 16px;background:#fde8e8;color:#b91c1c;font-weight:bold;'
        f'font-family:Arial,Helvetica,sans-serif;font-size:14px;text-align:center;">'
        f"Over Risk: {over_risk_count}</td>"
        f'<td style="padding:12px 16px;background:#f3f4f6;color:#374151;font-weight:bold;'
        f'font-family:Arial,Helvetica,sans-serif;font-size:14px;text-align:center;">'
        f"Total: {total_count}</td>"
        "</tr></table>"
    )
    
    # Build table rows
    table_rows = []
    for item in items:
        # Format currency with dollar signs and thousand separators
        expected_budget_str = f"${item.expected_budget:,.2f}"
        actual_cost_str = f"${item.actual_cost:,.2f}"
        
        # Format utilization percentage with one decimal place
        utilization_str = f"{item.utilization_percentage}.0%"
        
        # Format variance with dollar sign and label
        if item.variance < 0:
            variance_str = f"-${abs(item.variance):,.2f} OVER BUDGET"
            variance_color = "#b91c1c"
        else:
            variance_str = f"${item.variance:,.2f} UNDER BUDGET"
            variance_color = "#15803d"
        
        # Status color
        if item.status == "ON_TRACK":
            status_color = "#15803d"
            status_bg = "#dcfce7"
        else:  # OVER_RISK
            status_color = "#b91c1c"
            status_bg = "#fde8e8"
        
        row = (
            "<tr>"
            f'<td style="padding:8px 10px;border:1px solid #e5e7eb;font-family:Arial,Helvetica,sans-serif;'
            f'font-size:13px;">{html.escape(item.job_name)}</td>'
            f'<td style="padding:8px 10px;border:1px solid #e5e7eb;font-family:Arial,Helvetica,sans-serif;'
            f'font-size:13px;">{html.escape(item.cost_code)}</td>'
            f'<td style="padding:8px 10px;border:1px solid #e5e7eb;font-family:Arial,Helvetica,sans-serif;'
            f'font-size:13px;">{html.escape(item.cost_code_description)}</td>'
            f'<td style="padding:8px 10px;border:1px solid #e5e7eb;background:{status_bg};'
            f'color:{status_color};font-weight:bold;font-family:Arial,Helvetica,sans-serif;'
            f'font-size:13px;text-align:center;">{html.escape(item.status)}</td>'
            f'<td style="padding:8px 10px;border:1px solid #e5e7eb;font-family:Arial,Helvetica,sans-serif;'
            f'font-size:13px;text-align:right;">{expected_budget_str}</td>'
            f'<td style="padding:8px 10px;border:1px solid #e5e7eb;font-family:Arial,Helvetica,sans-serif;'
            f'font-size:13px;text-align:right;">{actual_cost_str}</td>'
            f'<td style="padding:8px 10px;border:1px solid #e5e7eb;font-family:Arial,Helvetica,sans-serif;'
            f'font-size:13px;text-align:right;">{utilization_str}</td>'
            f'<td style="padding:8px 10px;border:1px solid #e5e7eb;color:{variance_color};'
            f'font-family:Arial,Helvetica,sans-serif;font-size:13px;text-align:right;">{variance_str}</td>'
            "</tr>"
        )
        table_rows.append(row)
    
    # Budget items table
    budget_table = (
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        'style="width:100%;max-width:900px;border-collapse:collapse;margin-bottom:16px;">'
        '<thead><tr style="background:#f3f4f6;">'
        '<th style="padding:8px 10px;border:1px solid #e5e7eb;text-align:left;'
        'font-family:Arial,Helvetica,sans-serif;font-size:12px;color:#374151;">Job</th>'
        '<th style="padding:8px 10px;border:1px solid #e5e7eb;text-align:left;'
        'font-family:Arial,Helvetica,sans-serif;font-size:12px;color:#374151;">Cost Code</th>'
        '<th style="padding:8px 10px;border:1px solid #e5e7eb;text-align:left;'
        'font-family:Arial,Helvetica,sans-serif;font-size:12px;color:#374151;">Description</th>'
        '<th style="padding:8px 10px;border:1px solid #e5e7eb;text-align:center;'
        'font-family:Arial,Helvetica,sans-serif;font-size:12px;color:#374151;">Status</th>'
        '<th style="padding:8px 10px;border:1px solid #e5e7eb;text-align:right;'
        'font-family:Arial,Helvetica,sans-serif;font-size:12px;color:#374151;">Expected Budget</th>'
        '<th style="padding:8px 10px;border:1px solid #e5e7eb;text-align:right;'
        'font-family:Arial,Helvetica,sans-serif;font-size:12px;color:#374151;">Actual Cost</th>'
        '<th style="padding:8px 10px;border:1px solid #e5e7eb;text-align:right;'
        'font-family:Arial,Helvetica,sans-serif;font-size:12px;color:#374151;">Utilization %</th>'
        '<th style="padding:8px 10px;border:1px solid #e5e7eb;text-align:right;'
        'font-family:Arial,Helvetica,sans-serif;font-size:12px;color:#374151;">Variance</th>'
        "</tr></thead><tbody>"
        + "".join(table_rows)
        + "</tbody></table>"
    )
    
    # Comments section
    comments_section = ""
    if comments:
        comments_section = (
            '<div style="margin:16px 0;padding:12px;background:#f9fafb;border-left:4px solid #3b82f6;">'
            '<p style="margin:0 0 4px 0;font-weight:bold;font-size:13px;color:#374151;'
            'font-family:Arial,Helvetica,sans-serif;">Comments:</p>'
            f'<p style="margin:0;font-size:13px;color:#1f2937;font-family:Arial,Helvetica,sans-serif;">'
            f'{html.escape(comments)}</p>'
            '</div>'
        )
    
    # Footer
    footer = (
        '<p style="margin:24px 0 0 0;padding-top:16px;border-top:1px solid #e5e7eb;'
        'font-size:12px;color:#6b7280;font-family:Arial,Helvetica,sans-serif;">'
        f"Sent by Gould Construction APM — {html.escape(now_str)}"
        "</p>"
    )
    
    # Assemble HTML body
    html_body = (
        '<div style="font-family:Arial,Helvetica,sans-serif;color:#111827;max-width:900px;">'
        f'<h1 style="margin:0 0 8px 0;font-size:20px;color:#111827;">Budget Tracking Report</h1>'
        f'<p style="margin:0 0 16px 0;font-size:14px;color:#374151;">Date Range: <strong>'
        f"{html.escape(period_start)} to {html.escape(period_end)}</strong></p>"
        + summary_bar
        + budget_table
        + comments_section
        + footer
        + "</div>"
    )
    
    logger.debug("Budget email body built successfully")
    return subject, html_body
