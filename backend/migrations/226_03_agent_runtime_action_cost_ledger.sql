-- 226_03: independent Action Cost Ledger; never mixed with model settlements.
SET LOCAL ROLE everydayai_owner;
CREATE TABLE agent_action_cost_settlements (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(), action_id UUID NOT NULL REFERENCES agent_actions(id) ON DELETE RESTRICT,
 attempt_id UUID NOT NULL REFERENCES agent_action_attempts(id) ON DELETE RESTRICT,
 run_id UUID NOT NULL REFERENCES agent_runs(id) ON DELETE RESTRICT, org_id UUID REFERENCES organizations(id) ON DELETE RESTRICT,
 user_id UUID REFERENCES users(id) ON DELETE RESTRICT,
 kind TEXT NOT NULL CHECK(kind IN ('reserve','settle','release','refund','adjustment')),
 reserved_amount BIGINT NOT NULL DEFAULT 0 CHECK(reserved_amount>=0), actual_amount BIGINT NOT NULL DEFAULT 0 CHECK(actual_amount>=0),
 currency TEXT NOT NULL DEFAULT 'credits' CHECK(length(btrim(currency)) BETWEEN 1 AND 40),
 provider_receipt_hash TEXT CHECK(provider_receipt_hash IS NULL OR provider_receipt_hash ~ '^[0-9a-f]{64}$'),
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','applied','rejected')),
 reason_code TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(action_id,attempt_id,kind), UNIQUE(action_id,attempt_id,provider_receipt_hash)
);
ALTER TABLE agent_action_cost_settlements ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_action_cost_settlements FORCE ROW LEVEL SECURITY;
CREATE POLICY agent_action_cost_settlements_owner_all ON agent_action_cost_settlements FOR ALL TO everydayai_owner USING(TRUE) WITH CHECK(TRUE);
REVOKE ALL ON TABLE agent_action_cost_settlements FROM PUBLIC,everydayai_agent_runtime_worker,everydayai_worker,everydayai_runtime;

CREATE FUNCTION _record_agent_action_cost(p_action_id UUID,p_attempt_id UUID,p_kind TEXT,p_reserved BIGINT,p_actual BIGINT,p_reason TEXT,p_receipt TEXT)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE a agent_actions%ROWTYPE; t agent_action_attempts%ROWTYPE; s agent_action_cost_settlements%ROWTYPE;
BEGIN
 PERFORM _assert_agent_runtime_actor(TRUE);
 SELECT * INTO a FROM agent_actions WHERE id=p_action_id; SELECT * INTO t FROM agent_action_attempts WHERE id=p_attempt_id;
 IF a.id IS NULL OR t.id IS NULL OR t.action_id IS DISTINCT FROM a.id OR p_kind NOT IN ('reserve','settle','release','refund','adjustment') THEN RAISE EXCEPTION 'AGENT_ACTION_COST_BINDING_INVALID'; END IF;
 INSERT INTO agent_action_cost_settlements(action_id,attempt_id,run_id,org_id,user_id,kind,reserved_amount,actual_amount,reason_code,provider_receipt_hash,status)
 VALUES(a.id,t.id,a.run_id,a.org_id,a.user_id,p_kind,p_reserved,p_actual,p_reason,p_receipt,'applied') ON CONFLICT(action_id,attempt_id,kind) DO NOTHING RETURNING * INTO s;
 IF NOT FOUND THEN RETURN jsonb_build_object('outcome','duplicate'); END IF;
 PERFORM _agent_runtime_226_append_action_event(a.id,'action.cost.'||p_kind,jsonb_build_object('settlement_id',s.id,'amount',p_actual));
 RETURN jsonb_build_object('outcome','applied','settlement_id',s.id);
END; $$;
CREATE FUNCTION reserve_agent_action_cost(p_action_id UUID,p_attempt_id UUID,p_reserved_amount BIGINT,p_currency TEXT) RETURNS JSONB LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public AS $$ SELECT _record_agent_action_cost($1,$2,'reserve',$3,0,$4,NULL) $$;
CREATE FUNCTION settle_agent_action_cost(p_action_id UUID,p_attempt_id UUID,p_actual_amount BIGINT,p_currency TEXT,p_provider_receipt_hash TEXT) RETURNS JSONB LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public AS $$ SELECT _record_agent_action_cost($1,$2,'settle',0,$3,$4,$5) $$;
CREATE FUNCTION release_agent_action_cost(p_action_id UUID,p_attempt_id UUID,p_reason_code TEXT) RETURNS JSONB LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public AS $$ SELECT _record_agent_action_cost($1,$2,'release',0,0,$3,NULL) $$;
CREATE FUNCTION refund_agent_action_cost(p_action_id UUID,p_attempt_id UUID,p_reason_code TEXT) RETURNS JSONB LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public AS $$ SELECT _record_agent_action_cost($1,$2,'refund',0,0,$3,NULL) $$;
CREATE FUNCTION adjust_agent_action_cost(p_action_id UUID,p_attempt_id UUID,p_actual_amount BIGINT,p_reason_code TEXT,p_provider_receipt_hash TEXT) RETURNS JSONB LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public AS $$ SELECT _record_agent_action_cost($1,$2,'adjustment',0,$3,$4,$5) $$;
REVOKE ALL ON FUNCTION reserve_agent_action_cost(UUID,UUID,BIGINT,TEXT),settle_agent_action_cost(UUID,UUID,BIGINT,TEXT,TEXT),release_agent_action_cost(UUID,UUID,TEXT),refund_agent_action_cost(UUID,UUID,TEXT),adjust_agent_action_cost(UUID,UUID,BIGINT,TEXT,TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION reserve_agent_action_cost(UUID,UUID,BIGINT,TEXT),settle_agent_action_cost(UUID,UUID,BIGINT,TEXT,TEXT),release_agent_action_cost(UUID,UUID,TEXT),refund_agent_action_cost(UUID,UUID,TEXT),adjust_agent_action_cost(UUID,UUID,BIGINT,TEXT,TEXT) TO everydayai_agent_runtime_worker;
RESET ROLE;
