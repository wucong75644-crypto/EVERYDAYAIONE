-- 226_02: redacted, signed, idempotent provider callbacks.
SET LOCAL ROLE everydayai_owner;
CREATE TABLE agent_action_callback_inbox (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    provider TEXT NOT NULL CHECK (length(btrim(provider)) BETWEEN 1 AND 100),
    provider_event_id TEXT NOT NULL CHECK (length(btrim(provider_event_id)) BETWEEN 1 AND 300),
    callback_correlation TEXT NOT NULL CHECK (length(btrim(callback_correlation)) BETWEEN 1 AND 300),
    action_id UUID REFERENCES agent_actions(id) ON DELETE RESTRICT,
    attempt_id UUID REFERENCES agent_action_attempts(id) ON DELETE RESTRICT,
    payload_hash TEXT NOT NULL CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
    payload_redacted JSONB NOT NULL CHECK (jsonb_typeof(payload_redacted)='object' AND pg_column_size(payload_redacted)<=262144),
    signature_valid BOOLEAN NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    processed_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','processed','rejected','dead')),
    error_code TEXT,
    UNIQUE(provider, provider_event_id, payload_hash)
);
ALTER TABLE agent_action_callback_inbox ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_action_callback_inbox FORCE ROW LEVEL SECURITY;
CREATE POLICY agent_action_callback_inbox_owner_all ON agent_action_callback_inbox
 FOR ALL TO everydayai_owner USING(TRUE) WITH CHECK(TRUE);
REVOKE ALL ON TABLE agent_action_callback_inbox FROM PUBLIC, everydayai_agent_runtime_worker, everydayai_worker, everydayai_runtime;

CREATE FUNCTION record_agent_action_callback(
 p_provider TEXT, p_provider_event_id TEXT, p_callback_correlation TEXT,
 p_payload_hash TEXT, p_payload_redacted JSONB, p_signature_valid BOOLEAN,
 p_action_id UUID DEFAULT NULL, p_attempt_id UUID DEFAULT NULL
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE i agent_action_callback_inbox%ROWTYPE;
BEGIN
 PERFORM _assert_agent_runtime_actor(TRUE);
 IF NOT p_signature_valid OR p_payload_redacted ?| ARRAY['token','secret','password','cookie','authorization']
    OR p_payload_hash !~ '^[0-9a-f]{64}$' THEN RAISE EXCEPTION 'AGENT_CALLBACK_REJECTED' USING ERRCODE='22023'; END IF;
 INSERT INTO agent_action_callback_inbox(provider,provider_event_id,callback_correlation,payload_hash,payload_redacted,signature_valid,action_id,attempt_id)
 VALUES(btrim(p_provider),btrim(p_provider_event_id),btrim(p_callback_correlation),p_payload_hash,p_payload_redacted,TRUE,p_action_id,p_attempt_id)
 ON CONFLICT(provider,provider_event_id,payload_hash) DO NOTHING RETURNING * INTO i;
 IF NOT FOUND THEN RETURN jsonb_build_object('outcome','duplicate'); END IF;
 RETURN jsonb_build_object('outcome','accepted','inbox_id',i.id);
END; $$;
REVOKE ALL ON FUNCTION record_agent_action_callback(TEXT,TEXT,TEXT,TEXT,JSONB,BOOLEAN,UUID,UUID) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION record_agent_action_callback(TEXT,TEXT,TEXT,TEXT,JSONB,BOOLEAN,UUID,UUID) TO everydayai_agent_runtime_worker;
RESET ROLE;
