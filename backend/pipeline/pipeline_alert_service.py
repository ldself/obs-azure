"""Standalone pipeline alert service (Phase 2, OI-DI-04).

Fires both a Microsoft Teams incoming webhook AND an SMTP email for every
pipeline alert event (COMPLETED, REJECTED, QUARANTINED). Both channels fire
independently for every call: a failure in one must not suppress the other.

All delivery errors are caught, logged at WARNING level, and suppressed —
never re-raised (RULE 7). A failed alert must never roll back the pipeline
transaction or prevent the ingestion result from being persisted.

Environment variables (read at call time, not at module import):
    TEAMS_WEBHOOK_URL   — incoming webhook URL for the OBS pipeline channel.
                          Skip Teams silently if unset or empty.
    EMAIL_FROM          — sender address.
    EMAIL_TO            — comma-separated recipient list.
    EMAIL_SMTP_HOST     — SMTP relay hostname. Skip email if unset or empty.
    SMTP_PORT           — SMTP port (default 587).
    SMTP_USER           — SMTP auth username (optional).
    SMTP_PASSWORD       — SMTP auth password (optional).
"""

from __future__ import annotations

import json
import logging
import os
import smtplib
from email.mime.text import MIMEText
from typing import Any
from urllib.error import URLError
from urllib.request import Request
from urllib.request import urlopen

logger = logging.getLogger("obs.pipeline.alerts")


def send_pipeline_alert(
    *,
    event_type: str,
    file_name: str,
    file_type: str,
    ingestion_id: str,
    total_rows: int | None = None,
    error_rate: float | None = None,
    quarantined_rows: int | None = None,
    error_detail: str | None = None,
) -> None:
    """Fire Teams webhook and SMTP email for a pipeline alert event (OI-DI-04).

    Both channels are attempted independently. Failures of either channel are
    caught, logged at WARNING, and suppressed (RULE 7).
    """
    subject = f"OBS Pipeline {event_type}: {file_name}"
    body = _build_body(
        event_type=event_type,
        file_name=file_name,
        file_type=file_type,
        ingestion_id=ingestion_id,
        total_rows=total_rows,
        error_rate=error_rate,
        quarantined_rows=quarantined_rows,
        error_detail=error_detail,
    )

    webhook_url = os.environ.get("TEAMS_WEBHOOK_URL", "").strip()
    if webhook_url:
        try:
            payload = _build_teams_payload(
                event_type=event_type,
                file_name=file_name,
                file_type=file_type,
                ingestion_id=ingestion_id,
                body=body,
            )
            _send_teams(webhook_url, payload)
        except Exception as exc:
            logger.warning("Teams alert failed for ingestion %s: %s", ingestion_id, exc)
    else:
        logger.debug("TEAMS_WEBHOOK_URL not configured — skipping Teams alert")

    smtp_host = os.environ.get("EMAIL_SMTP_HOST", "").strip()
    if smtp_host:
        try:
            _send_email(subject=subject, body=body)
        except Exception as exc:
            logger.warning("Email alert failed for ingestion %s: %s", ingestion_id, exc)
    else:
        logger.debug("EMAIL_SMTP_HOST not configured — skipping email alert")


def _build_body(
    *,
    event_type: str,
    file_name: str,
    file_type: str,
    ingestion_id: str,
    total_rows: int | None,
    error_rate: float | None,
    quarantined_rows: int | None,
    error_detail: str | None,
) -> str:
    lines = [
        f"Event:         {event_type}",
        f"File:          {file_name}",
        f"Type:          {file_type}",
        f"Ingestion ID:  {ingestion_id}",
    ]
    if total_rows is not None:
        lines.append(f"Total rows:    {total_rows}")
    if quarantined_rows is not None:
        lines.append(f"Quarantined:   {quarantined_rows}")
    if error_rate is not None:
        lines.append(f"Error rate:    {error_rate:.2%}")
    if error_detail:
        lines.append(f"Detail:        {error_detail}")
    return "\n".join(lines)


def _build_teams_payload(
    *,
    event_type: str,
    file_name: str,
    file_type: str,
    ingestion_id: str,
    body: str,
) -> dict[str, Any]:
    color = {
        "COMPLETED": "00AA00",
        "REJECTED": "CC0000",
        "QUARANTINED": "FFA500",
    }.get(event_type, "808080")
    return {
        "@type": "MessageCard",
        "@context": "http://schema.org/extensions",
        "themeColor": color,
        "summary": f"OBS Pipeline {event_type}: {file_name}",
        "sections": [
            {
                "activityTitle": f"**OBS Pipeline — {event_type}**",
                "activitySubtitle": f"{file_type} · {file_name}",
                "text": f"```\n{body}\n```",
            }
        ],
    }


def _send_teams(webhook_url: str, payload: dict[str, Any]) -> None:
    data = json.dumps(payload).encode("utf-8")
    req = Request(
        webhook_url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(req, timeout=10) as resp:
        if resp.status not in (200, 201, 202):
            raise URLError(f"Teams webhook returned HTTP {resp.status}")


def _send_email(*, subject: str, body: str) -> None:
    smtp_host = os.environ.get("EMAIL_SMTP_HOST", "")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER", "")
    smtp_password = os.environ.get("SMTP_PASSWORD", "")
    email_from = os.environ.get("EMAIL_FROM", "obs-pipeline@example.com")
    email_to_raw = os.environ.get("EMAIL_TO", "")
    recipients = [r.strip() for r in email_to_raw.split(",") if r.strip()]
    if not recipients:
        logger.debug("EMAIL_TO not configured — skipping email alert")
        return

    msg = MIMEText(body, "plain")
    msg["Subject"] = subject
    msg["From"] = email_from
    msg["To"] = ", ".join(recipients)

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.ehlo()
        if smtp_port != 25:
            server.starttls()
        if smtp_user and smtp_password:
            server.login(smtp_user, smtp_password)
        server.sendmail(email_from, recipients, msg.as_string())
