"""
Stripe payment-link adapter. The agent never reads card digits over voice — we
generate a one-shot checkout link and SMS it to the caller. PCI scope is held
to the customer's browser/wallet.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import stripe
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings

from .base import load_integration


async def create_link(
    session: AsyncSession,
    *,
    org_id: UUID,
    amount_cents: int,
    description: str,
    customer_email: str | None = None,
) -> dict[str, Any]:
    s = get_settings()
    api_key = s.stripe_secret_key
    loaded = await load_integration(session, org_id=org_id, provider="stripe")
    if loaded:
        creds, _ = loaded
        api_key = creds.get("secret_key") or api_key
    if not api_key:
        return {
            "payment_link": f"{s.public_dashboard_url}/pay/demo/{amount_cents}",
            "expires_in": 900,
            "demo": True,
        }

    stripe.api_key = api_key
    session_obj = stripe.checkout.Session.create(
        mode="payment",
        line_items=[
            {
                "price_data": {
                    "currency": "usd",
                    "unit_amount": amount_cents,
                    "product_data": {"name": description[:128]},
                },
                "quantity": 1,
            }
        ],
        success_url=f"{s.public_dashboard_url}/pay/success",
        cancel_url=f"{s.public_dashboard_url}/pay/cancel",
        customer_email=customer_email,
        expires_at=None,
    )
    return {
        "payment_link": session_obj.url,
        "session_id": session_obj.id,
        "expires_in": 86400,
    }
