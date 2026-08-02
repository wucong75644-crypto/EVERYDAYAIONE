-- 226_15: preserve exact RPC resolution for JSON numeric arguments from the worker adapter.
SET LOCAL ROLE everydayai_owner;

CREATE FUNCTION record_agent_action_cost_strict(
    p_action_id UUID, p_attempt_id UUID, p_kind TEXT,
    p_reserved_amount SMALLINT, p_actual_amount SMALLINT, p_currency TEXT,
    p_reason_code TEXT, p_provider_receipt_hash TEXT
) RETURNS JSONB LANGUAGE sql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
    SELECT record_agent_action_cost_strict(
        p_action_id, p_attempt_id, p_kind,
        p_reserved_amount::BIGINT, p_actual_amount::BIGINT, p_currency,
        p_reason_code, p_provider_receipt_hash)
$$;

CREATE FUNCTION record_agent_action_cost_strict(
    p_action_id UUID, p_attempt_id UUID, p_kind TEXT,
    p_reserved_amount INTEGER, p_actual_amount INTEGER, p_currency TEXT,
    p_reason_code TEXT, p_provider_receipt_hash TEXT
) RETURNS JSONB LANGUAGE sql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
    SELECT record_agent_action_cost_strict(
        p_action_id, p_attempt_id, p_kind,
        p_reserved_amount::BIGINT, p_actual_amount::BIGINT, p_currency,
        p_reason_code, p_provider_receipt_hash)
$$;

REVOKE ALL ON FUNCTION record_agent_action_cost_strict(
    UUID,UUID,TEXT,SMALLINT,SMALLINT,TEXT,TEXT,TEXT),
    record_agent_action_cost_strict(
    UUID,UUID,TEXT,INTEGER,INTEGER,TEXT,TEXT,TEXT)
FROM PUBLIC, everydayai_worker, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_sync, everydayai;
GRANT EXECUTE ON FUNCTION record_agent_action_cost_strict(
    UUID,UUID,TEXT,SMALLINT,SMALLINT,TEXT,TEXT,TEXT),
    record_agent_action_cost_strict(
    UUID,UUID,TEXT,INTEGER,INTEGER,TEXT,TEXT,TEXT)
TO everydayai_agent_runtime_worker;
RESET ROLE;
