SET LOCAL ROLE everydayai_owner;

CREATE TABLE agent_runtime_scheduled_run_credit_budgets(
 runtime_run_id UUID PRIMARY KEY REFERENCES agent_runs(id) ON DELETE RESTRICT,
 scheduled_run_id UUID NOT NULL UNIQUE REFERENCES scheduled_task_runs(id) ON DELETE RESTRICT,
 scheduled_task_id UUID NOT NULL REFERENCES scheduled_tasks(id) ON DELETE RESTRICT,
 org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
 user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
 profile_state_version BIGINT NOT NULL CHECK(profile_state_version>0),
 max_credits INTEGER NOT NULL CHECK(max_credits>=0),
 reserved_credits INTEGER NOT NULL DEFAULT 0 CHECK(reserved_credits>=0),
 settled_credits INTEGER NOT NULL DEFAULT 0 CHECK(settled_credits>=0),
 pending_adjustment_credits INTEGER NOT NULL DEFAULT 0 CHECK(pending_adjustment_credits>=0),
 adjusted_credits INTEGER NOT NULL DEFAULT 0 CHECK(adjusted_credits>=0),
 state_version BIGINT NOT NULL DEFAULT 0 CHECK(state_version>=0),
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 CHECK(reserved_credits+settled_credits+pending_adjustment_credits+adjusted_credits<=max_credits)
);

CREATE TABLE agent_runtime_scheduled_model_credit_allocations(
 model_step_id UUID PRIMARY KEY REFERENCES agent_model_steps(id) ON DELETE RESTRICT,
 runtime_run_id UUID NOT NULL REFERENCES agent_runtime_scheduled_run_credit_budgets(runtime_run_id) ON DELETE RESTRICT,
 settlement_id UUID NOT NULL UNIQUE REFERENCES agent_model_credit_settlements(id) ON DELETE RESTRICT,
 reservation_attempt_id UUID NOT NULL REFERENCES agent_model_attempts(id) ON DELETE RESTRICT,
 idempotency_key TEXT NOT NULL,
 request_hash TEXT NOT NULL CHECK(request_hash~'^[0-9a-f]{64}$'),
 status TEXT NOT NULL CHECK(status IN('reserved','settled','released','adjustment_pending','adjusted')),
 reserved_credits INTEGER NOT NULL CHECK(reserved_credits>=0),
 settled_credits INTEGER NOT NULL DEFAULT 0 CHECK(settled_credits>=0),
 pending_adjustment_credits INTEGER NOT NULL DEFAULT 0 CHECK(pending_adjustment_credits>=0),
 adjusted_credits INTEGER NOT NULL DEFAULT 0 CHECK(adjusted_credits>=0),
 state_version BIGINT NOT NULL DEFAULT 0 CHECK(state_version>=0),
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 CHECK(length(btrim(idempotency_key)) BETWEEN 1 AND 300)
);

CREATE TABLE agent_runtime_scheduled_credit_overages(
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
 user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
 runtime_run_id UUID NOT NULL REFERENCES agent_runtime_scheduled_run_credit_budgets(runtime_run_id) ON DELETE RESTRICT,
 model_step_id UUID NOT NULL REFERENCES agent_model_steps(id) ON DELETE RESTRICT,
 attempt_id UUID NOT NULL REFERENCES agent_model_attempts(id) ON DELETE RESTRICT,
 settlement_id UUID NOT NULL REFERENCES agent_model_credit_settlements(id) ON DELETE RESTRICT,
 request_hash TEXT NOT NULL CHECK(request_hash~'^[0-9a-f]{64}$'),
 receipt_hash TEXT NOT NULL CHECK(receipt_hash~'^[0-9a-f]{64}$'),
 provider_actual_credits INTEGER NOT NULL CHECK(provider_actual_credits>0),
 user_charge_credits INTEGER NOT NULL CHECK(user_charge_credits>=0),
 overage_credits INTEGER NOT NULL CHECK(overage_credits>0),
 status TEXT NOT NULL DEFAULT 'reconcile_required' CHECK(status='reconcile_required'),
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(settlement_id,receipt_hash),
 CHECK(provider_actual_credits=user_charge_credits+overage_credits)
);

ALTER TABLE agent_runtime_scheduled_run_credit_budgets ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_scheduled_run_credit_budgets FORCE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_scheduled_model_credit_allocations ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_scheduled_model_credit_allocations FORCE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_scheduled_credit_overages ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_scheduled_credit_overages FORCE ROW LEVEL SECURITY;
CREATE POLICY runtime_scheduled_run_credit_budgets_owner_all
 ON agent_runtime_scheduled_run_credit_budgets FOR ALL TO everydayai_owner USING(TRUE) WITH CHECK(TRUE);
CREATE POLICY runtime_scheduled_model_credit_allocations_owner_all
 ON agent_runtime_scheduled_model_credit_allocations FOR ALL TO everydayai_owner USING(TRUE) WITH CHECK(TRUE);
CREATE POLICY runtime_scheduled_credit_overages_owner_all
 ON agent_runtime_scheduled_credit_overages FOR ALL TO everydayai_owner USING(TRUE) WITH CHECK(TRUE);
REVOKE ALL ON TABLE agent_runtime_scheduled_run_credit_budgets,
 agent_runtime_scheduled_model_credit_allocations,agent_runtime_scheduled_credit_overages FROM PUBLIC,everydayai_runtime,
 everydayai_wecom_runtime,everydayai_worker,everydayai_sync,everydayai,
 everydayai_agent_runtime_worker;

CREATE FUNCTION _agent_runtime_scheduled_budget_fact_guard() RETURNS TRIGGER
LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_BUDGET_FACT_IMMUTABLE' USING ERRCODE='55000'; END IF;
 IF TG_TABLE_NAME='agent_runtime_scheduled_credit_overages' THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_OVERAGE_IMMUTABLE' USING ERRCODE='55000';
 ELSIF TG_TABLE_NAME='agent_runtime_scheduled_run_credit_budgets' THEN
  IF (OLD.runtime_run_id,OLD.scheduled_run_id,OLD.scheduled_task_id,OLD.org_id,OLD.user_id,
      OLD.profile_state_version,OLD.max_credits,OLD.created_at) IS DISTINCT FROM
     (NEW.runtime_run_id,NEW.scheduled_run_id,NEW.scheduled_task_id,NEW.org_id,NEW.user_id,
      NEW.profile_state_version,NEW.max_credits,NEW.created_at)
   OR NEW.state_version<=OLD.state_version THEN
   RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_BUDGET_IDENTITY_IMMUTABLE' USING ERRCODE='55000';
  END IF;
 ELSE
  IF (OLD.model_step_id,OLD.runtime_run_id,OLD.settlement_id,OLD.reservation_attempt_id,
      OLD.idempotency_key,OLD.request_hash,OLD.created_at) IS DISTINCT FROM
     (NEW.model_step_id,NEW.runtime_run_id,NEW.settlement_id,NEW.reservation_attempt_id,
      NEW.idempotency_key,NEW.request_hash,NEW.created_at)
   OR NEW.state_version<=OLD.state_version THEN
   RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_BUDGET_ALLOCATION_IMMUTABLE' USING ERRCODE='55000';
  END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER runtime_scheduled_run_credit_budget_guard BEFORE UPDATE OR DELETE
 ON agent_runtime_scheduled_run_credit_budgets FOR EACH ROW EXECUTE FUNCTION _agent_runtime_scheduled_budget_fact_guard();
CREATE TRIGGER runtime_scheduled_model_credit_allocation_guard BEFORE UPDATE OR DELETE
 ON agent_runtime_scheduled_model_credit_allocations FOR EACH ROW EXECUTE FUNCTION _agent_runtime_scheduled_budget_fact_guard();
CREATE TRIGGER runtime_scheduled_credit_overage_guard BEFORE UPDATE OR DELETE
 ON agent_runtime_scheduled_credit_overages FOR EACH ROW EXECUTE FUNCTION _agent_runtime_scheduled_budget_fact_guard();

CREATE FUNCTION _agent_runtime_scheduled_budget_project_settlement() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE a agent_runtime_scheduled_model_credit_allocations%ROWTYPE;
 b agent_runtime_scheduled_run_credit_budgets%ROWTYPE;
 old_reserved INTEGER:=0;old_settled INTEGER:=0;old_pending INTEGER:=0;old_adjusted INTEGER:=0;
 new_reserved INTEGER:=0;new_settled INTEGER:=0;new_pending INTEGER:=0;new_adjusted INTEGER:=0;
BEGIN
 SELECT * INTO a FROM agent_runtime_scheduled_model_credit_allocations WHERE model_step_id=NEW.model_step_id FOR UPDATE;
 IF a.model_step_id IS NULL THEN
  IF EXISTS(SELECT 1 FROM agent_model_steps s JOIN agent_runtime_scheduled_run_bindings x
   ON x.runtime_run_id=s.run_id AND x.owner_kind='runtime' WHERE s.id=NEW.model_step_id) THEN
   RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_BUDGET_ALLOCATION_REQUIRED' USING ERRCODE='42501';
  END IF;
  RETURN NEW;
 END IF;
 SELECT * INTO b FROM agent_runtime_scheduled_run_credit_budgets WHERE runtime_run_id=a.runtime_run_id FOR UPDATE;
 IF b.runtime_run_id IS NULL OR a.settlement_id IS DISTINCT FROM NEW.id
 OR a.reservation_attempt_id IS DISTINCT FROM NEW.reservation_attempt_id
 OR a.reserved_credits IS DISTINCT FROM OLD.reserved_credits THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_BUDGET_SETTLEMENT_FENCED' USING ERRCODE='42501';
 END IF;
 old_reserved:=CASE WHEN OLD.status='reserved' THEN OLD.reserved_credits ELSE 0 END;
 old_settled:=CASE WHEN OLD.status='settled' THEN OLD.settled_credits ELSE 0 END;
 old_pending:=CASE WHEN OLD.status='adjustment_pending' THEN OLD.adjusted_credits ELSE 0 END;
 old_adjusted:=CASE WHEN OLD.status='adjusted' THEN OLD.adjusted_credits ELSE 0 END;
 new_reserved:=CASE WHEN NEW.status='reserved' THEN NEW.reserved_credits ELSE 0 END;
 new_settled:=CASE WHEN NEW.status='settled' THEN NEW.settled_credits ELSE 0 END;
 new_pending:=CASE WHEN NEW.status='adjustment_pending' THEN NEW.adjusted_credits ELSE 0 END;
 new_adjusted:=CASE WHEN NEW.status='adjusted' THEN NEW.adjusted_credits ELSE 0 END;
 IF b.reserved_credits-old_reserved+new_reserved+b.settled_credits-old_settled+new_settled+
    b.pending_adjustment_credits-old_pending+new_pending+b.adjusted_credits-old_adjusted+new_adjusted>b.max_credits THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_BUDGET_EXHAUSTED' USING ERRCODE='P34BE';
 END IF;
 UPDATE agent_runtime_scheduled_run_credit_budgets SET
  reserved_credits=reserved_credits-old_reserved+new_reserved,
  settled_credits=settled_credits-old_settled+new_settled,
  pending_adjustment_credits=pending_adjustment_credits-old_pending+new_pending,
  adjusted_credits=adjusted_credits-old_adjusted+new_adjusted,
  state_version=state_version+1,updated_at=clock_timestamp() WHERE runtime_run_id=b.runtime_run_id;
 UPDATE agent_runtime_scheduled_model_credit_allocations SET status=NEW.status,
  settled_credits=NEW.settled_credits,
  pending_adjustment_credits=CASE WHEN NEW.status='adjustment_pending' THEN NEW.adjusted_credits ELSE 0 END,
  adjusted_credits=CASE WHEN NEW.status='adjusted' THEN NEW.adjusted_credits ELSE 0 END,
  state_version=state_version+1,updated_at=clock_timestamp() WHERE model_step_id=NEW.model_step_id;
 RETURN NEW;
END $$;
CREATE TRIGGER runtime_scheduled_model_credit_projection BEFORE UPDATE
 ON agent_model_credit_settlements FOR EACH ROW EXECUTE FUNCTION _agent_runtime_scheduled_budget_project_settlement();

DO $$ BEGIN
 IF EXISTS(
  SELECT 1 FROM agent_model_credit_settlements settlement
  JOIN agent_model_steps step ON step.id=settlement.model_step_id
  JOIN agent_runtime_scheduled_run_bindings binding ON binding.runtime_run_id=step.run_id
  WHERE binding.owner_kind='runtime'
 ) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_BUDGET_HISTORICAL_FACTS_EXIST' USING ERRCODE='55000';
 END IF;
END $$;

ALTER FUNCTION prepare_model_attempt(UUID,UUID,BIGINT,TEXT,TEXT,TEXT,TEXT,JSONB,INTEGER,INTEGER)
 RENAME TO _prepare_model_attempt_without_scheduled_budget_v1;
ALTER FUNCTION _settle_agent_model_credits(agent_model_steps,UUID,TEXT,INTEGER)
 RENAME TO _settle_agent_model_credits_without_scheduled_budget_v1;
ALTER FUNCTION _release_agent_model_credits(UUID)
 RENAME TO _release_agent_model_credits_without_scheduled_budget_v1;
ALTER FUNCTION _adjust_model_attempt_credits(UUID,TEXT,INTEGER)
 RENAME TO _adjust_model_attempt_credits_without_scheduled_budget_v1;

CREATE FUNCTION prepare_model_attempt(
 p_step_id UUID,p_run_execution_token UUID,p_expected_step_version BIGINT,p_worker_id TEXT,
 p_request_hash TEXT,p_idempotency_key TEXT,p_provider TEXT,p_request_receipt JSONB,
 p_reserved_credits INTEGER,p_lease_seconds INTEGER DEFAULT 120) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE s agent_model_steps%ROWTYPE;r agent_runs%ROWTYPE;b agent_runtime_scheduled_run_bindings%ROWTYPE;
 e agent_runtime_scheduled_execution_profiles%ROWTYPE;t scheduled_tasks%ROWTYPE;q scheduled_task_runs%ROWTYPE;
 c agent_model_credit_settlements%ROWTYPE;a agent_model_attempts%ROWTYPE;budget agent_runtime_scheduled_run_credit_budgets%ROWTYPE;
 result JSONB;max_value INTEGER;inserted INTEGER;
BEGIN
 PERFORM _assert_agent_runtime_actor(TRUE);
 SELECT * INTO s FROM agent_model_steps WHERE id=p_step_id;
 IF s.id IS NULL THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
 PERFORM 1 FROM agent_runtime_sessions WHERE id=s.session_id FOR UPDATE;
 SELECT * INTO r FROM agent_runs WHERE id=s.run_id FOR UPDATE;
 SELECT * INTO s FROM agent_model_steps WHERE id=p_step_id FOR UPDATE;
 IF r.run_kind<>'scheduled' THEN
  RETURN _prepare_model_attempt_without_scheduled_budget_v1(p_step_id,p_run_execution_token,p_expected_step_version,p_worker_id,
   p_request_hash,p_idempotency_key,p_provider,p_request_receipt,p_reserved_credits,p_lease_seconds);
 END IF;
 IF r.status<>'running' OR r.execution_token IS DISTINCT FROM p_run_execution_token THEN
  RETURN jsonb_build_object('outcome','ownership_lost');
 END IF;
 IF r.lease_expires_at<=clock_timestamp() THEN RETURN jsonb_build_object('outcome','lease_expired'); END IF;
 IF s.status<>'running' OR s.state_version<>p_expected_step_version THEN
  RETURN jsonb_build_object('outcome','stale_version');
 END IF;
 IF p_request_hash!~'^[0-9a-f]{64}$' OR NULLIF(btrim(p_worker_id),'') IS NULL
 OR NULLIF(btrim(p_idempotency_key),'') IS NULL OR NULLIF(btrim(p_provider),'') IS NULL
 OR jsonb_typeof(p_request_receipt) IS DISTINCT FROM 'object'
 OR p_lease_seconds NOT BETWEEN 15 AND 600 THEN
  RAISE EXCEPTION 'AGENT_MODEL_ATTEMPT_INVALID' USING ERRCODE='22023';
 END IF;
 IF EXISTS(SELECT 1 FROM agent_model_credit_settlements WHERE model_step_id=s.id)
 AND NOT EXISTS(SELECT 1 FROM agent_runtime_scheduled_model_credit_allocations WHERE model_step_id=s.id) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_BUDGET_ALLOCATION_REQUIRED' USING ERRCODE='42501';
 END IF;
 SELECT * INTO b FROM agent_runtime_scheduled_run_bindings WHERE runtime_run_id=r.id;
 SELECT * INTO e FROM agent_runtime_scheduled_execution_profiles WHERE scheduled_task_id=b.scheduled_task_id;
 SELECT * INTO t FROM scheduled_tasks WHERE id=b.scheduled_task_id;
 SELECT * INTO q FROM scheduled_task_runs WHERE id=b.scheduled_run_id;
 IF b.scheduled_run_id IS NULL OR e.scheduled_task_id IS NULL OR t.id IS NULL OR q.id IS NULL
 OR b.owner_kind<>'runtime' OR b.scheduled_task_id IS DISTINCT FROM t.id
 OR (b.org_id,b.user_id) IS DISTINCT FROM(r.org_id,r.user_id)
 OR (t.org_id,t.user_id) IS DISTINCT FROM(r.org_id,r.user_id)
 OR (q.task_id,q.org_id) IS DISTINCT FROM(t.id,r.org_id)
 OR b.profile_state_version IS DISTINCT FROM e.state_version
 OR r.config_snapshot->'scheduled_budget' IS DISTINCT FROM e.budget_snapshot
 OR jsonb_typeof(r.config_snapshot->'scheduled_budget'->'max_credits') IS DISTINCT FROM 'number'
 OR (r.config_snapshot->'scheduled_budget'->>'max_credits')!~'^[0-9]+$'
 OR jsonb_typeof(e.budget_snapshot->'max_credits') IS DISTINCT FROM 'number'
 OR (e.budget_snapshot->>'max_credits')!~'^[0-9]+$'
 OR (r.config_snapshot->'scheduled_budget'->>'max_credits')::INTEGER IS DISTINCT FROM
    (e.budget_snapshot->>'max_credits')::INTEGER THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_BUDGET_SOURCE_INVALID' USING ERRCODE='42501';
 END IF;
 max_value:=(e.budget_snapshot->>'max_credits')::INTEGER;
 INSERT INTO agent_runtime_scheduled_run_credit_budgets(runtime_run_id,scheduled_run_id,scheduled_task_id,
  org_id,user_id,profile_state_version,max_credits) VALUES(r.id,b.scheduled_run_id,b.scheduled_task_id,
  r.org_id,r.user_id,e.state_version,max_value) ON CONFLICT(runtime_run_id) DO NOTHING;
 SELECT * INTO budget FROM agent_runtime_scheduled_run_credit_budgets WHERE runtime_run_id=r.id FOR UPDATE;
 IF (budget.scheduled_run_id,budget.scheduled_task_id,budget.org_id,budget.user_id,
     budget.profile_state_version,budget.max_credits) IS DISTINCT FROM
    (b.scheduled_run_id,b.scheduled_task_id,r.org_id,r.user_id,e.state_version,max_value) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_BUDGET_FACT_CONFLICT' USING ERRCODE='42501';
 END IF;
 IF NOT EXISTS(SELECT 1 FROM agent_runtime_scheduled_model_credit_allocations WHERE model_step_id=s.id)
 AND p_reserved_credits>budget.max_credits-budget.reserved_credits-budget.settled_credits-
     budget.pending_adjustment_credits-budget.adjusted_credits THEN
  RETURN jsonb_build_object('outcome','budget_exhausted','max_credits',max_value,
   'remaining_credits',GREATEST(budget.max_credits-budget.reserved_credits-budget.settled_credits-
    budget.pending_adjustment_credits-budget.adjusted_credits,0));
 END IF;
 BEGIN
  result:=_prepare_model_attempt_without_scheduled_budget_v1(p_step_id,p_run_execution_token,p_expected_step_version,p_worker_id,
   p_request_hash,p_idempotency_key,p_provider,p_request_receipt,p_reserved_credits,p_lease_seconds);
  IF result->>'outcome' NOT IN('prepared','already_prepared') THEN RETURN result; END IF;
  SELECT * INTO c FROM agent_model_credit_settlements WHERE model_step_id=p_step_id FOR UPDATE;
  SELECT * INTO a FROM agent_model_attempts WHERE id=c.reservation_attempt_id;
  IF c.id IS NULL OR a.id IS NULL OR a.idempotency_key IS DISTINCT FROM p_idempotency_key
   OR a.request_hash IS DISTINCT FROM p_request_hash OR c.reserved_credits IS DISTINCT FROM p_reserved_credits THEN
   RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_BUDGET_SETTLEMENT_INVALID' USING ERRCODE='42501';
  END IF;
  INSERT INTO agent_runtime_scheduled_model_credit_allocations(model_step_id,runtime_run_id,settlement_id,
   reservation_attempt_id,idempotency_key,request_hash,status,reserved_credits,settled_credits,
   pending_adjustment_credits,adjusted_credits) VALUES(s.id,r.id,c.id,c.reservation_attempt_id,
   a.idempotency_key,a.request_hash,c.status,c.reserved_credits,c.settled_credits,
   CASE WHEN c.status='adjustment_pending' THEN c.adjusted_credits ELSE 0 END,
   CASE WHEN c.status='adjusted' THEN c.adjusted_credits ELSE 0 END)
  ON CONFLICT(model_step_id) DO NOTHING RETURNING 1 INTO inserted;
  IF inserted=1 THEN
   UPDATE agent_runtime_scheduled_run_credit_budgets SET
    reserved_credits=reserved_credits+CASE WHEN c.status='reserved' THEN c.reserved_credits ELSE 0 END,
    settled_credits=settled_credits+CASE WHEN c.status='settled' THEN c.settled_credits ELSE 0 END,
    pending_adjustment_credits=pending_adjustment_credits+CASE WHEN c.status='adjustment_pending' THEN c.adjusted_credits ELSE 0 END,
    adjusted_credits=adjusted_credits+CASE WHEN c.status='adjusted' THEN c.adjusted_credits ELSE 0 END,
    state_version=state_version+1,updated_at=clock_timestamp() WHERE runtime_run_id=r.id;
  ELSE
   PERFORM 1 FROM agent_runtime_scheduled_model_credit_allocations x WHERE x.model_step_id=s.id
    AND x.runtime_run_id=r.id AND x.settlement_id=c.id AND x.reservation_attempt_id=c.reservation_attempt_id
    AND x.idempotency_key=a.idempotency_key AND x.request_hash=a.request_hash AND x.reserved_credits=c.reserved_credits;
   IF NOT FOUND THEN RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_BUDGET_ALLOCATION_CONFLICT' USING ERRCODE='42501'; END IF;
  END IF;
 EXCEPTION WHEN SQLSTATE 'P34BE' THEN
  RETURN jsonb_build_object('outcome','budget_exhausted','max_credits',max_value,'remaining_credits',
   GREATEST(max_value-COALESCE(budget.reserved_credits+budget.settled_credits+
   budget.pending_adjustment_credits+budget.adjusted_credits,0),0));
 END;
 SELECT * INTO budget FROM agent_runtime_scheduled_run_credit_budgets WHERE runtime_run_id=r.id;
 RETURN result||jsonb_build_object('budget_max_credits',budget.max_credits,'budget_remaining_credits',
  budget.max_credits-budget.reserved_credits-budget.settled_credits-
  budget.pending_adjustment_credits-budget.adjusted_credits);
END $$;

CREATE FUNCTION _settle_agent_model_credits(p_step agent_model_steps,p_attempt_id UUID,
 p_response_hash TEXT,p_actual_credits INTEGER) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE a agent_runtime_scheduled_model_credit_allocations%ROWTYPE;result JSONB;
BEGIN
 SELECT * INTO a FROM agent_runtime_scheduled_model_credit_allocations WHERE model_step_id=p_step.id;
 IF a.model_step_id IS NULL THEN
  RETURN _settle_agent_model_credits_without_scheduled_budget_v1(
   p_step,p_attempt_id,p_response_hash,p_actual_credits);
 END IF;
 PERFORM 1 FROM agent_model_credit_settlements WHERE model_step_id=p_step.id FOR UPDATE;
 PERFORM 1 FROM agent_runtime_scheduled_model_credit_allocations WHERE model_step_id=p_step.id FOR UPDATE;
 PERFORM 1 FROM agent_runtime_scheduled_run_credit_budgets WHERE runtime_run_id=a.runtime_run_id FOR UPDATE;
 result:=_settle_agent_model_credits_without_scheduled_budget_v1(
  p_step,p_attempt_id,p_response_hash,p_actual_credits);
 RETURN result;
END $$;

CREATE FUNCTION _release_agent_model_credits(p_step_id UUID) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE a agent_runtime_scheduled_model_credit_allocations%ROWTYPE;result JSONB;
BEGIN
 SELECT * INTO a FROM agent_runtime_scheduled_model_credit_allocations WHERE model_step_id=p_step_id;
 IF a.model_step_id IS NULL THEN
  RETURN _release_agent_model_credits_without_scheduled_budget_v1(p_step_id);
 END IF;
 PERFORM 1 FROM agent_model_credit_settlements WHERE model_step_id=p_step_id FOR UPDATE;
 PERFORM 1 FROM agent_runtime_scheduled_model_credit_allocations WHERE model_step_id=p_step_id FOR UPDATE;
 PERFORM 1 FROM agent_runtime_scheduled_run_credit_budgets WHERE runtime_run_id=a.runtime_run_id FOR UPDATE;
 result:=_release_agent_model_credits_without_scheduled_budget_v1(p_step_id);
 RETURN result;
END $$;

CREATE FUNCTION _adjust_model_attempt_credits(p_attempt_id UUID,p_response_hash TEXT,p_actual_credits INTEGER)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE attempt agent_model_attempts%ROWTYPE;c agent_model_credit_settlements%ROWTYPE;
 a agent_runtime_scheduled_model_credit_allocations%ROWTYPE;
 b agent_runtime_scheduled_run_credit_budgets%ROWTYPE;
 prior agent_runtime_scheduled_credit_overages%ROWTYPE;result JSONB;
 old_value INTEGER:=0;used_without INTEGER;user_charge INTEGER;overage INTEGER;
BEGIN
 SELECT * INTO attempt FROM agent_model_attempts WHERE id=p_attempt_id;
 IF attempt.id IS NULL THEN
  RETURN _adjust_model_attempt_credits_without_scheduled_budget_v1(
   p_attempt_id,p_response_hash,p_actual_credits);
 END IF;
 SELECT * INTO a FROM agent_runtime_scheduled_model_credit_allocations
  WHERE model_step_id=attempt.model_step_id;
 IF a.model_step_id IS NULL OR p_actual_credits<0 THEN
  RETURN _adjust_model_attempt_credits_without_scheduled_budget_v1(
   p_attempt_id,p_response_hash,p_actual_credits);
 END IF;
 SELECT * INTO c FROM agent_model_credit_settlements
  WHERE model_step_id=attempt.model_step_id FOR UPDATE;
 SELECT * INTO a FROM agent_runtime_scheduled_model_credit_allocations
  WHERE model_step_id=attempt.model_step_id FOR UPDATE;
 SELECT * INTO b FROM agent_runtime_scheduled_run_credit_budgets
  WHERE runtime_run_id=a.runtime_run_id FOR UPDATE;
 IF c.id IS NULL OR b.runtime_run_id IS NULL OR a.settlement_id IS DISTINCT FROM c.id
 OR a.runtime_run_id IS DISTINCT FROM attempt.run_id THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_BUDGET_SETTLEMENT_FENCED' USING ERRCODE='42501';
 END IF;
 old_value:=CASE a.status WHEN 'reserved' THEN a.reserved_credits
  WHEN 'settled' THEN a.settled_credits
  WHEN 'adjustment_pending' THEN a.pending_adjustment_credits
  WHEN 'adjusted' THEN a.adjusted_credits ELSE 0 END;
 used_without:=b.reserved_credits+b.settled_credits+
  b.pending_adjustment_credits+b.adjusted_credits-old_value;
 user_charge:=LEAST(p_actual_credits,GREATEST(b.max_credits-used_without,0));
 overage:=p_actual_credits-user_charge;
 SELECT * INTO prior FROM agent_runtime_scheduled_credit_overages
  WHERE settlement_id=c.id AND receipt_hash=p_response_hash;
 IF prior.id IS NOT NULL AND
   (prior.org_id,prior.user_id,prior.runtime_run_id,prior.model_step_id,prior.attempt_id,
    prior.request_hash,prior.provider_actual_credits,prior.user_charge_credits,prior.overage_credits)
   IS DISTINCT FROM
   (attempt.org_id,attempt.user_id,attempt.run_id,attempt.model_step_id,attempt.id,
    attempt.request_hash,p_actual_credits,user_charge,overage) THEN
  RETURN jsonb_build_object('outcome','receipt_conflict');
 END IF;
 IF c.status='adjustment_pending' THEN
  IF c.response_hash IS DISTINCT FROM p_response_hash
  OR c.adjusted_credits IS DISTINCT FROM user_charge
  OR (overage>0 AND prior.id IS NULL)
  OR (overage=0 AND prior.id IS NOT NULL) THEN
   RETURN jsonb_build_object('outcome','receipt_conflict');
  END IF;
  RETURN jsonb_build_object('outcome','insufficient_credits',
   'provider_actual_credits',p_actual_credits,'user_charge_credits',user_charge,
   'overage_credits',overage);
 END IF;
 result:=_adjust_model_attempt_credits_without_scheduled_budget_v1(
  p_attempt_id,p_response_hash,user_charge);
 IF result->>'outcome' IN('adjusted','already_adjusted','insufficient_credits')
 AND overage>0 AND prior.id IS NULL THEN
  INSERT INTO agent_runtime_scheduled_credit_overages(org_id,user_id,runtime_run_id,
   model_step_id,attempt_id,settlement_id,request_hash,receipt_hash,
   provider_actual_credits,user_charge_credits,overage_credits)
  VALUES(attempt.org_id,attempt.user_id,attempt.run_id,attempt.model_step_id,attempt.id,
   c.id,attempt.request_hash,p_response_hash,p_actual_credits,user_charge,overage);
 END IF;
 RETURN result||jsonb_build_object('provider_actual_credits',p_actual_credits,
  'user_charge_credits',user_charge,'overage_credits',overage);
END $$;

REVOKE ALL ON FUNCTION _agent_runtime_scheduled_budget_fact_guard(),
 _agent_runtime_scheduled_budget_project_settlement(),
 _prepare_model_attempt_without_scheduled_budget_v1(UUID,UUID,BIGINT,TEXT,TEXT,TEXT,TEXT,JSONB,INTEGER,INTEGER),
 _settle_agent_model_credits_without_scheduled_budget_v1(agent_model_steps,UUID,TEXT,INTEGER),
 _release_agent_model_credits_without_scheduled_budget_v1(UUID),
 _adjust_model_attempt_credits_without_scheduled_budget_v1(UUID,TEXT,INTEGER),
 _settle_agent_model_credits(agent_model_steps,UUID,TEXT,INTEGER),
 _release_agent_model_credits(UUID),_adjust_model_attempt_credits(UUID,TEXT,INTEGER),
 prepare_model_attempt(UUID,UUID,BIGINT,TEXT,TEXT,TEXT,TEXT,JSONB,INTEGER,INTEGER)
 FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,everydayai_sync,everydayai,
 everydayai_agent_runtime_worker;
GRANT EXECUTE ON FUNCTION prepare_model_attempt(UUID,UUID,BIGINT,TEXT,TEXT,TEXT,TEXT,JSONB,INTEGER,INTEGER)
 TO everydayai_agent_runtime_worker;

RESET ROLE;
