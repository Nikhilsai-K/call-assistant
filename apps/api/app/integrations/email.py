"""
Email adapter. Postmark first, Resend on Postmark error / no creds.
Templates are rendered via str.format on the variables dict — simple and
auditable. Each org can override `from_email` in its Postmark integration row.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings

from .base import load_integration

# Built-in templates; orgs can override any of these via the templates table later.
TEMPLATES: dict[str, dict[str, str]] = {
    "appointment_confirmation": {
        "subject": "Your appointment is confirmed",
        "body": (
            "Hi {customer_name},\n\n"
            "Your {service} appointment is confirmed for {start_at_pretty}.\n\n"
            "We'll text {phone} a reminder the day before. To reschedule, reply to this email "
            "or call us back at {business_phone}.\n\n— {business_name}"
        ),
    },
    "review_request": {
        "subject": "How did we do?",
        "body": (
            "Hi {customer_name},\n\nThanks for choosing {business_name}. "
            "If you have a minute, we'd love a quick review: {review_link}"
        ),
    },
    "reschedule_confirmation": {
        "subject": "Your appointment was rescheduled",
        "body": (
            "Hi {customer_name},\n\nYour {service} appointment is now {start_at_pretty}.\n\n"
            "— {business_name}"
        ),
    },
}


def render(template: str, variables: dict[str, Any]) -> tuple[str, str]:
    spec = TEMPLATES.get(template)
    if spec is None:
        # Allow ad-hoc raw bodies via variables["__subject__"] / ["__body__"].
        return variables.get("__subject__", "Notice"), variables.get("__body__", "")
    return spec["subject"].format(**variables), spec["body"].format(**variables)


async def _postmark_send(
    api_token: str, *, from_email: str, to: str, subject: str, body: str
) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=10.0) as c:
        resp = await c.post(
            "https://api.postmarkapp.com/email",
            json={
                "From": from_email,
                "To": to,
                "Subject": subject,
                "TextBody": body,
                "MessageStream": "outbound",
            },
            headers={
                "Accept": "application/json",
                "X-Postmark-Server-Token": api_token,
            },
        )
        resp.raise_for_status()
        return {"sent": True, "provider": "postmark", "id": resp.json().get("MessageID")}


async def _resend_send(
    api_key: str, *, from_email: str, to: str, subject: str, body: str
) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=10.0) as c:
        resp = await c.post(
            "https://api.resend.com/emails",
            json={"from": from_email, "to": to, "subject": subject, "text": body},
            headers={"Authorization": f"Bearer {api_key}"},
        )
        resp.raise_for_status()
        return {"sent": True, "provider": "resend", "id": resp.json().get("id")}


async def send(
    session: AsyncSession,
    *,
    org_id: UUID,
    to: str,
    template: str,
    variables: dict[str, Any],
) -> dict[str, Any]:
    s = get_settings()
    subject, body = render(template, variables)

    # Postmark first.
    postmark_token = s.postmark_api_token
    from_email = s.postmark_from_email
    pm_loaded = await load_integration(session, org_id=org_id, provider="postmark")
    if pm_loaded:
        creds, _ = pm_loaded
        postmark_token = creds.get("api_token") or postmark_token
        from_email = creds.get("from_email") or from_email
    if postmark_token:
        try:
            return await _postmark_send(
                postmark_token, from_email=from_email, to=to, subject=subject, body=body
            )
        except Exception:
            pass

    # Resend fallback.
    resend_key = s.resend_api_key
    rs_loaded = await load_integration(session, org_id=org_id, provider="resend")
    if rs_loaded:
        creds, _ = rs_loaded
        resend_key = creds.get("api_key") or resend_key
    if resend_key:
        return await _resend_send(
            resend_key, from_email=from_email, to=to, subject=subject, body=body
        )

    return {"sent": False, "reason": "no_email_provider_configured"}
