# Architecture Decisions

Per build-prompt rule #8: "If a decision is needed, pick the more production-ready option and document it here."

---

## AD-001 — LiveKit Agents framework for session orchestration

**Context:** The spec forbids LangChain and mandates LiveKit for session orchestration. We use the LiveKit Agents Python SDK to own the room lifecycle (join, subscribe to audio, publish agent audio) and write a custom `CallSession` on top for the streaming STT→LLM→TTS pipeline.

**Why custom session vs. the SDK's built-in VoicePipeline:**
- We need fine-grained control over the latency budget (STT prewarming on partials, TTS-chunk streaming keyed to LLM sentence boundaries, filler injection at > 200ms TTFT).
- We need barge-in cancellation with deterministic cleanup of in-flight LLM + TTS tasks.
- We need tool-call interleaving that produces a backchannel mid-stream (LiveKit's built-in doesn't do this natively).

## AD-002 — Anthropic direct, not an abstraction layer

No LangChain, LiteLLM, or Semantic Kernel. `anthropic` client directly, because prompt caching (system + tools blocks) is fiddly through abstractions and every dropped cache-hit is a ~60% token regression.

## AD-003 — Streaming everything

`apps/agent/vocalflow_agent/pipeline/`:
- STT: Deepgram Nova-3 **websocket streaming** with `interim_results=true`, `endpointing=100ms`.
- LLM: `AsyncAnthropic.messages.stream()` — we yield on `content_block_delta` text_delta and on tool_use input_json_delta for incremental JSON parsing.
- TTS: ElevenLabs `/stream?optimize_streaming_latency=3&output_format=pcm_16000`.

We flush TTS on the **first sentence boundary** in the LLM stream, so first-audio lands long before the full response is generated.

## AD-004 — Semantic + silence endpointing

Silero VAD alone over-waits on thoughtful pauses and under-waits on "…and, um, thursday would…". We combine VAD (hard 900ms silence) with a lexical/punctuation heuristic (`pipeline/endpointing.py`). Upgrade path: a small classifier fine-tuned on real call transcripts; stub is in place.

## AD-005 — Overlapping STT finalize and LLM prewarm

High-confidence partial transcripts kick off a speculative LLM call before Deepgram's final result. On endpoint, whichever path's output we commit to is kept; the loser is cancelled. This trims ~80–120ms off TTFT. Enforced as a regression test (`test_overlapping_saves_time_vs_sequential`).

## AD-006 — Compliance gates are code paths, not flags

`services/compliance.py` raises `ComplianceViolation` from a centralized `gate_outbound_call`. It is called from the API before enqueueing and re-validated in the outbound worker (defense-in-depth). There is no "test bypass" — sandbox mode uses simulated calls that still traverse these checks.

## AD-007 — Row-level security for tenant isolation

All tenanted tables enable RLS with `USING (org_id = current_setting('app.current_org_id'))`. The API sets `app.current_org_id` via `set_config` per session (`db/session.get_session(org_id)`). Defense-in-depth against app-level bugs.

## AD-008 — TimescaleDB hypertable on `calls`

Calls table is the biggest source of long-tail analytical queries ("calls by hour by org"). We partition on `started_at` from day one to avoid a painful retrofit. Retention policies (90 days default, configurable per-org) are enforced via Timescale policies and an S3 lifecycle rule on recordings.

## AD-009 — Redis Streams for decoupled work queues

The API enqueues to streams (`vocalflow.inbound.pstn`, `vocalflow.outbound.requests`, `vocalflow.postcall.jobs`, `vocalflow.kb.index`). Agent and worker processes consume via consumer groups (`xreadgroup`, `xack`). This gives us natural idempotency, replay, and observability without introducing Kafka.

## AD-010 — Prompt caching on system prompt + tools

Every in-call turn writes the system prompt and full tool schema with `cache_control: ephemeral`. In production this cuts input-token cost ~60% and TTFT ~30ms on warm sessions. See `pipeline/llm.py`.

## AD-011 — Voice stacking strategy

- Primary: ElevenLabs Flash 2.5 (voice identity, 50–120ms first-audio).
- Fallback: Cartesia Sonic (auto on ElevenLabs HTTP 5xx or > 2s no-first-chunk timeout).
- Emergency: AWS Polly (operator-triggered only; documented in runbook).

`pipeline/tts.py` implements primary→fallback; the emergency path is left to ops because its quality jump is too visible to trigger silently.

## AD-012 — Per-call cost reconciliation in worker

Each leg writes its cost contribution to Redis (`vocalflow.call_cost.<call_id>` hash) as it happens. The post-call worker sums them and writes to `calls.*_cost_cents` atomically. This avoids racing updates from the agent process mid-call and gives us a single authoritative number per call.

## AD-013 — Warm Handoff 2.0 brief generated on Sonnet, not Haiku

`pipeline/llm.ClaudeStreamer.generate_handoff_brief()` uses the post-call Sonnet model for better summarization. 200ms latency is acceptable here because the human receiver is the consumer — the customer experience during the brief is hold music, not dead air.

## AD-014 — PCI flow is DTMF-only

`collect_payment` tool never reads digits off voice. It generates a Stripe Checkout link + SMS. If the org explicitly needs on-call capture (rare), we'd pause recording and route DTMF to a Twilio Pay verb — not the agent. This keeps us out of PCI scope creep.

## AD-015 — Shadow Mode = read-only participant

During onboarding (first 14 days), the agent joins real human calls as a silent LiveKit subscriber. Its outputs only populate proposed KB diffs, never audio. Customer-side consent notice is required and audited in `consent_records`.

## AD-016 — Golden eval suite as a first-class artifact

`apps/agent/.../tests/golden_scenarios.json` holds 10+ scripted scenarios. The full replay job runs on every prompt or model change and must score ≥ 4.0 avg on LLM-as-judge (Opus 4.7) to merge. The shape test runs on every PR.

## AD-017 — Dashboard uses SSE for live transcripts, not WebSocket

Browsers reconnect SSE automatically; our transcript is strictly server-push. We don't need the duplex channel for this use case. LiveKit WebRTC handles the bi-directional audio separately.

## AD-018 — Auth via Clerk JWTs, dev-only bypass header

The dashboard gets Clerk JWTs; API verifies against Clerk JWKS. Dev mode accepts an `X-Dev-Org` header and only in `ENV=development` — guarded in `core/auth.current_principal`.

## AD-019 — Fly.io regional workers for agent process

Agent workers run in the same region as the LiveKit room (us-east, us-west, eu-west). This keeps media RTT sub-50ms in each region. API and DB are multi-region primary with Postgres read-replicas.

## AD-020 — Feature flags are default-off when they risk latency

Features like emotion-routing and voice-biometrics are flagged and off unless a latency regression test covering that flag's code path is passing.
