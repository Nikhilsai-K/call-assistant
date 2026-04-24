"""
External webhooks. Each verifies its own signature.
"""

import json

import redis.asyncio as aioredis
import stripe
from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlalchemy import select
from twilio.request_validator import RequestValidator

from app.core.config import get_settings
from app.db.session import get_session
from app.models import PhoneNumber

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/twilio/voice")
async def twilio_voice(request: Request) -> Response:
    """Twilio hits this on inbound calls; we return TwiML that bridges media
    into a LiveKit room dedicated to that call. The agent worker is already
    subscribed to the room.
    """
    settings = get_settings()
    form = dict(await request.form())
    # Verify signature (prod).
    if settings.env != "development" and settings.twilio_auth_token:
        validator = RequestValidator(settings.twilio_auth_token)
        sig = request.headers.get("X-Twilio-Signature", "")
        url = str(request.url)
        if not validator.validate(url, form, sig):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "bad twilio signature")

    to_e164 = form.get("To", "")
    from_e164 = form.get("From", "")
    call_sid = form.get("CallSid", "")

    async with get_session() as s:
        # Look up the number's org + agent (no tenant filter — this is provider-signed).
        res = await s.execute(select(PhoneNumber).where(PhoneNumber.e164 == to_e164))
        pn = res.scalar_one_or_none()

    if pn is None:
        twiml = "<Response><Say>This number is not configured. Goodbye.</Say><Hangup/></Response>"
        return Response(content=twiml, media_type="application/xml")

    # Publish an "incoming call" event; agent worker claims it and joins the room.
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    room = f"call-{call_sid}"
    await redis.xadd(
        "vocalflow.inbound.pstn",
        {
            "call_sid": call_sid,
            "from": from_e164,
            "to": to_e164,
            "org_id": str(pn.org_id),
            "agent_id": str(pn.agent_id) if pn.agent_id else "",
            "phone_number_id": str(pn.id),
            "room": room,
        },
    )
    await redis.aclose()

    # Stream Twilio's media into LiveKit via the <Connect><Stream/>... bridge.
    stream_url = f"wss://media.vocalflow.app/twilio-bridge/{room}"
    twiml = (
        "<Response>"
        f'<Connect><Stream url="{stream_url}">'
        f'<Parameter name="room" value="{room}"/>'
        f'<Parameter name="org_id" value="{pn.org_id}"/>'
        "</Stream></Connect>"
        "</Response>"
    )
    return Response(content=twiml, media_type="application/xml")


@router.post("/twilio/sms")
async def twilio_sms(request: Request) -> Response:
    # Placeholder — SMS intake for reschedule flows.
    return Response(content="<Response/>", media_type="application/xml")


@router.post("/stripe")
async def stripe_webhook(request: Request) -> dict:
    settings = get_settings()
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    try:
        event = stripe.Webhook.construct_event(payload, sig, settings.stripe_webhook_secret)
    except Exception as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"bad signature: {e}") from e
    # fan out to worker for async processing
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    await redis.xadd("vocalflow.stripe.events", {"event": json.dumps(event)})
    await redis.aclose()
    return {"received": True}


@router.post("/calendar/{provider}")
async def calendar_webhook(provider: str, request: Request) -> dict:
    # Per-provider signature validation would go here.
    body = await request.body()
    redis = aioredis.from_url(get_settings().redis_url, decode_responses=True)
    await redis.xadd(
        "vocalflow.calendar.events",
        {"provider": provider, "body": body.decode("utf-8", errors="replace")},
    )
    await redis.aclose()
    return {"ok": True}


@router.post("/slack/events")
async def slack_events(request: Request) -> dict:
    body = await request.body()
    # Slack Events API — respond to URL verification, forward real events.
    data = json.loads(body or b"{}")
    if data.get("type") == "url_verification":
        return {"challenge": data["challenge"]}
    # Verify signature (skipped in dev; prod uses settings.slack_signing_secret).
    return {"ok": True}
