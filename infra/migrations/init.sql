-- VocalFlow initial schema
-- Idempotent so it can run as a docker-entrypoint-initdb.d script.

CREATE DATABASE langfuse;

\c vocalflow

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ========== organizations ==========
CREATE TABLE IF NOT EXISTS organizations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    clerk_org_id TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    plan TEXT NOT NULL DEFAULT 'starter',
    stripe_customer_id TEXT,
    hipaa_mode BOOLEAN NOT NULL DEFAULT FALSE,
    pci_mode BOOLEAN NOT NULL DEFAULT FALSE,
    recording_retention_days INTEGER NOT NULL DEFAULT 90,
    twilio_subaccount_sid TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ========== agents ==========
CREATE TABLE IF NOT EXISTS agents (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    system_prompt TEXT NOT NULL,
    voice_id TEXT NOT NULL,
    voice_provider TEXT NOT NULL,
    llm_model TEXT NOT NULL DEFAULT 'claude-haiku-4-5',
    tools_enabled JSONB NOT NULL DEFAULT '[]'::jsonb,
    kb_id UUID,
    business_hours JSONB,
    after_hours_agent_id UUID REFERENCES agents(id),
    emergency_keywords TEXT[] NOT NULL DEFAULT '{}',
    greeting_audio_s3_key TEXT,
    personality JSONB NOT NULL DEFAULT '{"formality":0.5,"pace":0.5,"warmth":0.7,"verbosity":0.4}'::jsonb,
    version INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'draft',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS agents_org_idx ON agents(org_id);

-- ========== phone_numbers ==========
CREATE TABLE IF NOT EXISTS phone_numbers (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    e164 TEXT UNIQUE NOT NULL,
    provider TEXT NOT NULL,
    provider_sid TEXT,
    agent_id UUID REFERENCES agents(id),
    inbound_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    outbound_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS phone_numbers_org_idx ON phone_numbers(org_id);

-- ========== calls ==========
-- Hypertable on started_at; we keep a UNIQUE(id) so child FKs can reference id alone.
CREATE TABLE IF NOT EXISTS calls (
    id UUID NOT NULL DEFAULT uuid_generate_v4(),
    org_id UUID NOT NULL,
    agent_id UUID,
    phone_number_id UUID,
    direction TEXT NOT NULL,
    from_e164 TEXT,
    to_e164 TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at TIMESTAMPTZ,
    duration_s INTEGER,
    recording_s3_key TEXT,
    outcome TEXT,
    outcome_details JSONB,
    sentiment_timeline JSONB,
    cost_cents INTEGER,
    stt_cost_cents INTEGER,
    llm_cost_cents INTEGER,
    tts_cost_cents INTEGER,
    twilio_cost_cents INTEGER,
    handoff_target TEXT,
    handoff_reason TEXT,
    shadow_mode BOOLEAN NOT NULL DEFAULT FALSE,
    livekit_room TEXT,
    langfuse_trace_id TEXT,
    quality_score NUMERIC(3,1),
    status TEXT NOT NULL DEFAULT 'pending',
    PRIMARY KEY (id, started_at),
    UNIQUE (id)
);
SELECT create_hypertable('calls', 'started_at', if_not_exists => TRUE, migrate_data => TRUE);
CREATE INDEX IF NOT EXISTS calls_org_started_idx ON calls(org_id, started_at DESC);
CREATE INDEX IF NOT EXISTS calls_agent_idx ON calls(agent_id);
CREATE INDEX IF NOT EXISTS calls_from_idx ON calls(from_e164);
CREATE INDEX IF NOT EXISTS calls_outcome_idx ON calls(outcome);
CREATE INDEX IF NOT EXISTS calls_room_idx ON calls(livekit_room);

-- ========== call_transcripts ==========
CREATE TABLE IF NOT EXISTS call_transcripts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    call_id UUID NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
    org_id UUID NOT NULL,
    speaker TEXT NOT NULL,
    text TEXT NOT NULL,
    start_ms INTEGER NOT NULL,
    end_ms INTEGER NOT NULL,
    is_redacted BOOLEAN NOT NULL DEFAULT FALSE,
    confidence NUMERIC(4,3),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS call_transcripts_call_idx ON call_transcripts(call_id, start_ms);
CREATE INDEX IF NOT EXISTS call_transcripts_org_idx ON call_transcripts(org_id);

-- ========== call_events ==========
CREATE TABLE IF NOT EXISTS call_events (
    id BIGSERIAL PRIMARY KEY,
    call_id UUID NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
    org_id UUID NOT NULL,
    type TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    ts_ms INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS call_events_call_idx ON call_events(call_id, ts_ms);
CREATE INDEX IF NOT EXISTS call_events_type_idx ON call_events(type);
CREATE INDEX IF NOT EXISTS call_events_org_idx ON call_events(org_id);

-- ========== knowledge_bases ==========
CREATE TABLE IF NOT EXISTS knowledge_bases (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_config JSONB NOT NULL DEFAULT '{}'::jsonb,
    last_synced_at TIMESTAMPTZ,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS kb_documents (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    kb_id UUID NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    content_tsv TSVECTOR,
    qdrant_point_id UUID,
    source_url TEXT,
    checksum TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS kb_documents_tsv_idx ON kb_documents USING gin(content_tsv);
CREATE INDEX IF NOT EXISTS kb_documents_kb_idx ON kb_documents(kb_id);
CREATE UNIQUE INDEX IF NOT EXISTS kb_documents_dedup_idx ON kb_documents(kb_id, checksum)
    WHERE checksum IS NOT NULL;

CREATE OR REPLACE FUNCTION kb_documents_tsv_trigger() RETURNS trigger AS $$
BEGIN
    NEW.content_tsv := to_tsvector('english', COALESCE(NEW.title,'') || ' ' || COALESCE(NEW.content,''));
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS kb_documents_tsv_update ON kb_documents;
CREATE TRIGGER kb_documents_tsv_update BEFORE INSERT OR UPDATE
    ON kb_documents FOR EACH ROW EXECUTE FUNCTION kb_documents_tsv_trigger();

-- ========== appointments ==========
CREATE TABLE IF NOT EXISTS appointments (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    call_id UUID REFERENCES calls(id) ON DELETE SET NULL,
    customer_name TEXT NOT NULL,
    phone TEXT NOT NULL,
    email TEXT,
    service TEXT NOT NULL,
    start_at TIMESTAMPTZ NOT NULL,
    duration_min INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'confirmed',
    external_provider TEXT,
    external_id TEXT,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS appointments_org_start_idx ON appointments(org_id, start_at);

-- ========== integrations ==========
CREATE TABLE IF NOT EXISTS integrations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    credentials_encrypted BYTEA,
    config_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'inactive',
    last_synced_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (org_id, provider)
);

-- ========== campaigns ==========
CREATE TABLE IF NOT EXISTS campaigns (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    agent_id UUID REFERENCES agents(id),
    script TEXT,
    target_list_id UUID,
    dnc_checked BOOLEAN NOT NULL DEFAULT FALSE,
    status TEXT NOT NULL DEFAULT 'draft',
    scheduled_at TIMESTAMPTZ,
    quiet_hours_enforced BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ========== dnc_list ==========
-- Intentionally global per spec: no org_id, no RLS. Operators import federal/state lists.
CREATE TABLE IF NOT EXISTS dnc_list (
    phone TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    added_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ========== consent_records ==========
CREATE TABLE IF NOT EXISTS consent_records (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    phone TEXT NOT NULL,
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    consent_type TEXT NOT NULL,
    consent_source TEXT NOT NULL,
    granted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    revoked_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS consent_records_phone_idx ON consent_records(phone, org_id, consent_type);

-- ========== audit_log ==========
CREATE TABLE IF NOT EXISTS audit_log (
    id BIGSERIAL PRIMARY KEY,
    org_id UUID,
    actor TEXT,
    action TEXT NOT NULL,
    target TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS audit_log_org_idx ON audit_log(org_id, created_at DESC);

-- ========== campaign_targets ==========
CREATE TABLE IF NOT EXISTS campaign_targets (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    campaign_id UUID NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    phone TEXT NOT NULL,
    name TEXT,
    variables JSONB NOT NULL DEFAULT '{}'::jsonb,
    state TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    last_attempt_at TIMESTAMPTZ,
    result JSONB
);
CREATE INDEX IF NOT EXISTS campaign_targets_status_idx ON campaign_targets(campaign_id, status);

-- ========== voiceprints (opt-in biometric) ==========
CREATE TABLE IF NOT EXISTS voiceprints (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    phone TEXT NOT NULL,
    embedding BYTEA NOT NULL,
    consent_record_id UUID REFERENCES consent_records(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (org_id, phone)
);

-- ========== Row-Level Security ==========
-- Tenants are keyed by org_id. The API sets app.current_org_id per request.
-- Policy uses COALESCE so an UNSET app.current_org_id evaluates to a sentinel
-- that matches no UUID — fail-closed.
ALTER TABLE agents ENABLE ROW LEVEL SECURITY;
ALTER TABLE phone_numbers ENABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge_bases ENABLE ROW LEVEL SECURITY;
ALTER TABLE kb_documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE appointments ENABLE ROW LEVEL SECURITY;
ALTER TABLE integrations ENABLE ROW LEVEL SECURITY;
ALTER TABLE campaigns ENABLE ROW LEVEL SECURITY;
ALTER TABLE campaign_targets ENABLE ROW LEVEL SECURITY;
ALTER TABLE consent_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE voiceprints ENABLE ROW LEVEL SECURITY;
ALTER TABLE calls ENABLE ROW LEVEL SECURITY;
ALTER TABLE call_transcripts ENABLE ROW LEVEL SECURITY;
ALTER TABLE call_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY;

DO $$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'agents','phone_numbers','knowledge_bases','appointments',
        'integrations','campaigns','consent_records','voiceprints',
        'calls','call_transcripts','call_events','audit_log'
    ]
    LOOP
        EXECUTE format($f$
            DROP POLICY IF EXISTS tenant_isolation ON %I;
            CREATE POLICY tenant_isolation ON %I
            USING (org_id::text = COALESCE(current_setting('app.current_org_id', true), '__none__'))
            WITH CHECK (org_id::text = COALESCE(current_setting('app.current_org_id', true), '__none__'));
        $f$, t, t);
    END LOOP;
END$$;

-- kb_documents inherits via kb_id -> knowledge_bases.org_id; enforce via subquery.
DROP POLICY IF EXISTS tenant_isolation ON kb_documents;
CREATE POLICY tenant_isolation ON kb_documents
USING (
    kb_id IN (
        SELECT id FROM knowledge_bases
        WHERE org_id::text = COALESCE(current_setting('app.current_org_id', true), '__none__')
    )
);

DROP POLICY IF EXISTS tenant_isolation ON campaign_targets;
CREATE POLICY tenant_isolation ON campaign_targets
USING (
    campaign_id IN (
        SELECT id FROM campaigns
        WHERE org_id::text = COALESCE(current_setting('app.current_org_id', true), '__none__')
    )
);
