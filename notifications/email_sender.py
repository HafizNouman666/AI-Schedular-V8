from __future__ import annotations

import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logger = logging.getLogger(__name__)


def _parse_recipients(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [addr.strip() for addr in raw.split(",") if addr.strip()]


#def send_time_log_report_email(subject: str, html_body: str) -> None:
#    """
#    Send an HTML email using SMTP settings from the environment.
#
#    Expected env vars:
#      SMTP_HOST, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD,
#      EMAIL_RECIPIENTS (comma-separated), SMTP_FROM_NAME (optional display name).
#    """
#    host = os.environ.get("SMTP_HOST", "").strip()
#    port_str = os.environ.get("SMTP_PORT", "587").strip()
#    username = os.environ.get("SMTP_USERNAME", "").strip()
#    password = os.environ.get("SMTP_PASSWORD", "")
#    from_name = os.environ.get("SMTP_FROM_NAME", "Gould APM Bot").strip()
#    recipients = _parse_recipients(os.environ.get("EMAIL_RECIPIENTS"))
#
#    if not host:
#        raise ValueError("SMTP_HOST is not set")
#    if not username:
#        raise ValueError("SMTP_USERNAME is not set")
#    if not recipients:
#        raise ValueError("EMAIL_RECIPIENTS is not set or empty")
#
#    try:
#        port = int(port_str)
#    except ValueError as e:
#        raise ValueError(f"SMTP_PORT must be an integer, got {port_str!r}") from e
#
#    logger.info(
#        "Sending email: subject=%r recipients=%s smtp=%s:%s",
#        subject, recipients, host, port,
#    )
#
#    msg = MIMEMultipart("alternative")
#    msg["Subject"] = subject
#    msg["From"] = formataddr((from_name, username))
#    msg["To"] = ", ".join(recipients)
#    msg.attach(MIMEText(html_body, "html", "utf-8"))
#
#    try:
#        with smtplib.SMTP(host, port, timeout=60) as server:
#            server.starttls()
#            if password:
#                server.login(username, password)
#            server.sendmail(username, recipients, msg.as_string())
#        logger.info(
#            "Email sent successfully: subject=%r to=%s",
#            subject, recipients,
#        )
#    except smtplib.SMTPAuthenticationError as exc:
#        logger.error("SMTP authentication failed for user=%s: %s", username, exc)
#        raise
#    except smtplib.SMTPException as exc:
#        logger.error("SMTP error while sending email: %s", exc)
#        raise
#    except OSError as exc:
#        logger.error("Network error connecting to SMTP %s:%s — %s", host, port, exc)
#        raise

def send_time_log_report_email(subject: str, html_body: str) -> None:
    """Send time log report via Microsoft Graph API."""
    from notifications.graph_email_sender import send_graph_email

    recipients_raw = os.environ.get("EMAIL_RECIPIENTS", "")
    recipients = [r.strip() for r in recipients_raw.split(",") if r.strip()]

    if not recipients:
        raise ValueError("EMAIL_RECIPIENTS is not set or empty")

    logger.info("Sending time log report: subject=%r recipients=%s", subject, recipients)
    send_graph_email(subject=subject, html_body=html_body, recipients=recipients)
