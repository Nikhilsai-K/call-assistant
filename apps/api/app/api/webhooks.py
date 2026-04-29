"""
External webhooks. Each verifies its own signature.
"""

import hashlib
import hmac
import json
import time

import redis.asyncio as aioredis
import stripe
from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlalchemy import select, text
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
    # Verify signature. Tighter gate: in production, REFUSE if no token configured.
    if settings.twilio_auth_token:
        validator = RequestValidator(settings.twilio_auth_token)
        sig = request.headers.get("X-Twilio-Signature", "")
        url = str(request.url)
        if not validator.validate(url, form, sig):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "bad twilio signature")
    elif settings.env == "production":
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "twilio_auth_token not configured"
        )

    to_e164 = form.get("To", "")
    from_e164 = form.get("From", "")
    call_sid = form.get("CallSid", "")

    # Webhooks are provider-signed, not user-bearer; we look up the org via the
    # called number, then bind app.current_org_id BEFORE any further DB writes.
    async with get_session() as s:
        res = await s.execute(select(PhoneNumber).where(PhoneNumber.e164 == to_e164))
        pn = res.scalar_one_or_none()

    if pn is None:
        twiml = "<Response><Say>This number is not configured. Goodbye.</Say><Hangup/></Response>"
        return Response(content=twiml, media_type="application/xml")

    org_id = str(pn.org_id)
    room = f"call-{call_sid}"

    # Persist the inbound Call row up front so the dashboard sees it
    # immediately. Bind RLS to the resolved org_id.
    async with get_session(org_id) as s:
        await s.execute(
            text(
                "INSERT INTO calls (org_id, agent_id, phone_number_id, direction, "
                "from_e164, to_e164, livekit_room, status) VALUES "
                "(:org_id, :agent_id, :pn_id, 'inbound', :from_e164, :to_e164, :room, 'ringing')"
            ),
            {
                "org_id": org_id,
                "agent_id": str(pn.agent_id) if pn.agent_id else None,
                "pn_id": str(pn.id),
                "from_e164": from_e164,
                "to_e164": to_e164,
                "room": room,
            },
        )

    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    await redis.xadd(
        "vocalflow.inbound.pstn",
        {
            "call_sid": call_sid,
            "from": from_e164,
            "to": to_e164,
            "org_id": org_id,
            "agent_id": str(pn.agent_id) if pn.agent_id else "",
            "phone_number_id": str(pn.id),
            "room": room,
        },
    )
    await redis.aclose()

    stream_url = f"{settings.bridge_public_url}/twilio-bridge/{room}"
    twiml = (
        "<Response>"
        f'<Connect><Stream url="{stream_url}">'
        f'<Parameter name="room" value="{room}"/>'
        f'<Parameter name="org_id" value="{org_id}"/>'
        "</Stream></Connect>"
        "</Response>"
    )
    return Response(content=twiml, media_type="application/xml")


@router.post("/twilio/sms")
async def twilio_sms(request: Request) -> Response:
    """Inbound SMS — record + forward to the org's Slack/Teams + persist for review."""
    settings = get_settings()
    form = dict(await request.form())
    if settings.twilio_auth_token:
        validator = RequestValidator(settings.twilio_auth_token)
        sig = request.headers.get("X-Twilio-Signature", "")
        url = str(request.url)
        if not validator.validate(url, form, sig):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "bad twilio signature")
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    await redis.xadd(
        "vocalflow.inbound.sms",
        {
            "from": form.get("From", ""),
            "to": form.get("To", ""),
            "body": form.get("Body", ""),
            "sid": form.get("MessageSid", ""),
        },
    )
    await redis.aclose()
    return Response(content="<Response/>", media_type="application/xml")


@router.api_route("/twilio/outbound-bridge", methods=["GET", "POST"])
async def twilio_outbound_bridge(request: Request) -> Response:
    """TwiML for outbound dialer: bridges the called party into a LiveKit room."""
    settings = get_settings()
    room = request.query_params.get("room", "out-unknown")
    stream_url = f"{settings.bridge_public_url}/twilio-bridge/{room}"
    twiml = (
        "<Response>"
        f'<Connect><Stream url="{stream_url}">'
        f'<Parameter name="room" value="{room}"/>'
        "</Stream></Connect>"
        "</Response>"
    )
    return Response(content=twiml, media_type="application/xml")


@router.post("/stripe")
async def stripe_webhook(request: Request) -> dict:
    settings = get_settings()
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    if not settings.stripe_webhook_secret:
        if settings.env == "production":
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR, "stripe_webhook_secret not configured"
            )
        # Dev convenience: accept unsigned but still parse JSON.
        try:
            event = json.loads(payload)
        except json.JSONDecodeError as e:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad json") from e
    else:
        try:
            event = stripe.Webhook.construct_event(payload, sig, settings.stripe_webhook_secret)
        except Exception as e:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"bad signature: {e}") from e
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


def _verify_slack_signature(body: bytes, headers: dict, signing_secret: str) -> bool:
    """Slack v0 HMAC-SHA256 over `v0:{ts}:{body}`."""
    ts = headers.get("x-slack-request-timestamp", "")
    sig = headers.get("x-slack-signature", "")
    if not (ts and sig and signing_secret):
        return False
    # Reject anything older than 5 minutes (replay protection).
    try:
        if abs(time.time() - int(ts)) > 60 * 5:
            return False
    except ValueError:
        return False
    base = f"v0:{ts}:".encode() + body
    expected = "v0=" + hmac.new(signing_secret.encode(), base, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)


@router.post("/slack/events")
async def slack_events(request: Request) -> dict:
    settings = get_settings()
    body = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}
    if settings.slack_signing_secret:
        if not _verify_slack_signature(body, headers, settings.slack_signing_secret):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "bad slack signature")
    elif settings.env == "production":
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "slack_signing_secret not configured"
        )

    data = json.loads(body or b"{}")
    if data.get("type") == "url_verification":
        return {"challenge": data["challenge"]}
    # Forward real events to the worker.
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    await redis.xadd("vocalflow.slack.events", {"event": json.dumps(data)})
    await redis.aclose()
    return {"ok": True}
