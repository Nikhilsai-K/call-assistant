# VocalFlow

Voice AI agent platform for SMB service businesses — sub-500ms latency, native integrations, shadow-mode onboarding. See the full product spec in `docs/PRODUCT_SPEC.md` (original build prompt).

## Monorepo layout

```
apps/
  api/      FastAPI control plane (agents, calls, KB, integrations, webhooks, tools)
  agent/    LiveKit Agents runtime (streaming STT->LLM->TTS loop, barge-in)
  bridge/   Twilio Media Streams <-> LiveKit audio bridge (µ-law <-> PCM)
  worker/   Celery post-call pipeline (redaction, summary, CRM sync, KB index/ingest, callbacks)
  web/      Next.js 15 dashboard (live transcript, supervisor, agent designer)
  widget/   Embeddable <script> for any customer website
infra/
  migrations/    Postgres + TimescaleDB schema
  terraform/     Fly.io / Cloudflare IaC (placeholders for V1)
.github/workflows/ci.yml
```

## Quick start (local dev)

```bash
cp .env.example .env    # fill in provider keys (or leave blank — many paths have fallbacks)
docker compose up -d postgres redis minio qdrant langfuse
docker compose up api agent worker web
```

Then:
- Dashboard: http://localhost:3000
- API: http://localhost:8000/docs
- MinIO console: http://localhost:9001 (`minioadmin`/`minioadmin`)
- Langfuse: http://localhost:3100
- Qdrant: http://localhost:6333

## Hard requirements (non-negotiable)

These are enforced in code and CI:

1. **p95 end-to-end latency < 500ms.** `apps/agent/.../test_latency_budget.py` asserts it; CI fails on regression.
2. **No dead air on failures.** Tool timeouts → backchannel ("one sec — my system's a little slow"). STT/LLM/TTS failures → graceful fallback utterance + transfer.
3. **Compliance gates cannot be bypassed.** `apps/api/.../services/compliance.py` hard-blocks outbound when: phone is on DNC, no prior consent record exists, or outside quiet hours (local to called number). No feature flag disables this.
4. **PCI mode** diverts card entry to DTMF; voice agent never reads or stores card digits.
5. **HIPAA mode** (per-org toggle) gates PHI redaction pre-LLM and tighter retention.
6. **Every call has a full audit trace** in Langfuse and `call_events`.

## Key flows

### Inbound PSTN call
```
Twilio number hit → POST /v1/webhooks/twilio/voice → TwiML <Connect><Stream> into LiveKit room
→ Redis stream vocalflow.inbound.pstn fires → agent worker claims job
→ CallSession: Deepgram Nova-3 streaming → Claude Haiku 4.5 (prompt-cached) → ElevenLabs Flash 2.5
→ on call end → Redis vocalflow.postcall.jobs → worker: redact → summarize (Sonnet 4.5) → CRM sync
```

### Outbound call
```
POST /v1/calls/outbound → api.compliance.gate_outbound_call
  → DNC check + prior-consent check + quiet-hours check (FAILS loudly, no bypass)
→ Redis vocalflow.outbound.requests → agent worker places via Twilio
```

### No-code agent designer
```
POST /v1/agents  { name, plain_instructions, personality }
→ services/prompt_compiler.compile_prompt(plain, personality) runs Sonnet 4.5
→ structured { system_prompt, tools_enabled, emergency_keywords, greeting } persisted
```

### Live dashboard
```
GET /v1/calls/{id}/transcript/stream  (SSE)
  ← subscribes Redis pubsub vocalflow.calls.{id}.events (agent publishes every turn)
POST /v1/calls/{id}/whisper  → redis.publish → agent injects context into next LLM turn
POST /v1/calls/{id}/takeover → agent speaks 20s brief → bridges human in
```

## Feature flags

Anything that can push latency above budget is flag-guarded and **off** until its own regression test passes. See `.env.example` `FEATURE_*`.

## Tests

```bash
# API
cd apps/api && pip install -e ".[dev]" && pytest

# Agent (includes latency regression)
cd apps/agent && pip install -e ".[dev]" && pytest

# Worker
cd apps/worker && pip install -e ".[dev]" && pytest

# Dashboard
cd apps/web && pnpm test
```

## Phase 1 status (weeks 1-4)

- [x] FastAPI control plane with full DB schema (Postgres + TimescaleDB)
- [x] Row-level security per-tenant
- [x] LiveKit voice-agent scaffold (streaming STT→LLM→TTS, barge-in, tools, backchannels)
- [x] Compliance gates (DNC, TCPA, 2-party consent, quiet hours)
- [x] Post-call worker (redaction, summary, cost reconciliation, LLM-as-judge sampling)
- [x] Dashboard skeleton with live SSE transcript + whisper/takeover
- [x] Golden scenarios & latency regression in CI
- [ ] Twilio ↔ LiveKit media bridge (placeholder TwiML — hook up Stream ingress next)
- [ ] Calendar tool integration (Google Calendar OAuth scaffolded; wiring in Phase 1 tail)

See `ARCHITECTURE_DECISIONS.md` for rationale and `docs/ROADMAP.md` for phases 2-5.
