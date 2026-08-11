SET LOCAL ROLE everydayai_owner;

DO $$
BEGIN
 IF EXISTS(SELECT 1 FROM agent_model_attempts
  WHERE model_tenant_kill_epoch IS NOT NULL
     OR model_provider_kill_epoch IS NOT NULL
     OR model_capability_kill_epoch IS NOT NULL) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_MODEL_DISPATCH_ROLLBACK_FACTS_EXIST';
 END IF;
END $$;

REVOKE ALL ON FUNCTION
 start_model_attempt_dispatch_v2(UUID,UUID,BIGINT,TEXT),
 get_agent_runtime_model_configuration_v1(UUID,UUID,TEXT,UUID,BIGINT,TEXT,TEXT)
FROM PUBLIC,everydayai_agent_runtime_worker,everydayai_worker,everydayai;
DROP FUNCTION get_agent_runtime_model_configuration_v1(
 UUID,UUID,TEXT,UUID,BIGINT,TEXT,TEXT);
DROP FUNCTION start_model_attempt_dispatch_v2(UUID,UUID,BIGINT,TEXT);
DROP FUNCTION _agent_runtime_model_dispatch_fences_v1(UUID,TEXT,TEXT);
ALTER TABLE agent_model_attempts
 DROP COLUMN model_capability_kill_epoch,
 DROP COLUMN model_provider_kill_epoch,
 DROP COLUMN model_tenant_kill_epoch;

DO $$
DECLARE signature TEXT;
BEGIN
 IF to_regrole('everydayai_agent_model_gateway') IS NULL THEN RETURN; END IF;
 GRANT USAGE ON SCHEMA public TO everydayai_agent_model_gateway;
 FOREACH signature IN ARRAY ARRAY[
  'public.read_agent_runtime_model_gateway_operation(uuid,uuid,uuid,uuid,uuid,uuid,text)',
  'public.claim_agent_runtime_model_gateway_operation_v2(uuid,text,text,uuid,uuid,uuid,uuid,uuid,text,bigint,text,text,text,text,text,bigint,bigint,bigint,integer)',
  'public.mark_agent_runtime_model_gateway_dispatched(uuid,uuid,bigint,uuid,text,text,bigint,bigint,bigint)',
  'public.renew_agent_runtime_model_gateway_operation(uuid,uuid,bigint,uuid,text,text,bigint,bigint,bigint,integer)',
  'public.finalize_agent_runtime_model_gateway_operation(uuid,uuid,bigint,uuid,text,text,bigint,bigint,bigint,text,text,boolean,text,jsonb,text,text)',
  'public.recover_agent_runtime_model_gateway_operations(text,integer,integer)',
  'public.fail_agent_runtime_model_gateway_claim(uuid,uuid,bigint,uuid,uuid,text,text,bigint,bigint,bigint,text)'
 ] LOOP
  IF to_regprocedure(signature) IS NOT NULL THEN
   EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO everydayai_agent_model_gateway',signature);
  END IF;
 END LOOP;
END $$;

RESET ROLE;
