"""
Agent worker entrypoint.

Two modes:
 - PSTN inbound: polls `vocalflow.inbound.pstn` Redis stream, claims a job,
   joins the matching LiveKit room, runs CallSession.
 - Outbound dispatch: polls `vocalflow.outbound.requests`, runs compliance
   checks again (defense-in-depth), places the call via Twilio, hooks into
   the same CallSession.

In both cases, audio flows through a LiveKit room. The PSTN <-> LiveKit bridge
runs as a separate process (Twilio Media Streams → LiveKit ingress).
"""

from __future__ import annotations

import asyncio
import signal
import sys
from uuid import uuid4

import redis.asyncio as aioredis
import structlog

from .config import settings
from .runtime.inbound import handle_inbound
from .runtime.outbound import handle_outbound

log = structlog.get_logger("agent.main")


async def inbound_loop() -> None:
    r = aioredis.from_url(settings.redis_url, decode_responses=True)
    stream = "vocalflow.inbound.pstn"
    group = "agent-workers"
    consumer = f"agent-{uuid4().hex[:6]}"
    try:
        await r.xgroup_create(stream, group, id="0", mkstream=True)
    except Exception:
        pass

    log.info("inbound_loop.started", consumer=consumer)
    while True:
        resp = await r.xreadgroup(group, consumer, {stream: ">"}, count=1, block=5000)
        if not resp:
            continue
        for _, entries in resp:
            for msg_id, fields in entries:
                try:
                    await handle_inbound(fields)
                    await r.xack(stream, group, msg_id)
                except Exception as e:
                    log.exception("inbound.failed", msg_id=msg_id, err=str(e))


async def outbound_loop() -> None:
    r = aioredis.from_url(settings.redis_url, decode_responses=True)
    stream = "vocalflow.outbound.requests"
    group = "agent-workers-outbound"
    consumer = f"agent-out-{uuid4().hex[:6]}"
    try:
        await r.xgroup_create(stream, group, id="0", mkstream=True)
    except Exception:
        pass

    log.info("outbound_loop.started", consumer=consumer)
    while True:
        resp = await r.xreadgroup(group, consumer, {stream: ">"}, count=1, block=5000)
        if not resp:
            continue
        for _, entries in resp:
            for msg_id, fields in entries:
                try:
                    await handle_outbound(fields)
                    await r.xack(stream, group, msg_id)
                except Exception as e:
                    log.exception("outbound.failed", msg_id=msg_id, err=str(e))


async def widget_loop() -> None:
    """Spawn an agent into LiveKit rooms requested by the public widget."""
    r = aioredis.from_url(settings.redis_url, decode_responses=True)
    stream = "vocalflow.inbound.widget"
    group = "agent-workers-widget"
    consumer = f"agent-web-{uuid4().hex[:6]}"
    try:
        await r.xgroup_create(stream, group, id="0", mkstream=True)
    except Exception:
        pass

    log.info("widget_loop.started", consumer=consumer)
    while True:
        resp = await r.xreadgroup(group, consumer, {stream: ">"}, count=1, block=5000)
        if not resp:
            continue
        for _, entries in resp:
            for msg_id, fields in entries:
                try:
                    # Same handler as PSTN — fields carry org_id, agent_id, room.
                    await handle_inbound({**fields, "from": "widget", "to": "widget"})
                    await r.xack(stream, group, msg_id)
                except Exception as e:
                    log.exception("widget.failed", msg_id=msg_id, err=str(e))


async def main() -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    tasks = [
        asyncio.create_task(inbound_loop()),
        asyncio.create_task(outbound_loop()),
        asyncio.create_task(widget_loop()),
    ]
    await stop.wait()
    for t in tasks:
        t.cancel()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
