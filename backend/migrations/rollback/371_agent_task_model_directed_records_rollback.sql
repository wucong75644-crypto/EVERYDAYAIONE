-- Do not strand new checkpoints on an old decoder. Disable new enrollment
-- first; retain this migration until every model-directed task is supported.
SET LOCAL ROLE everydayai;
-- DDL validates every row, including tenants hidden by FORCE RLS from the
-- migration session. A scoped SELECT EXISTS would incorrectly see an empty DB.
DO $$ BEGIN
 BEGIN
  ALTER TABLE public.agent_task_states ADD CONSTRAINT agent_task_direct_rollback_guard
    CHECK (state->'model_directed' IS DISTINCT FROM 'true'::jsonb);
 EXCEPTION WHEN check_violation THEN
  RAISE EXCEPTION 'TASK_DIRECT_COMPATIBLE_RUNTIME_REQUIRED';
 END;
END $$;

-- Optional notes/bootstrap may exist before the first concrete call/state row.
-- Validate all checkpoint tenants too; retain the original owner arrangement.
DO $$ DECLARE checkpoint_owner text; BEGIN
 SELECT pg_get_userbyid(relowner) INTO checkpoint_owner FROM pg_class WHERE oid='public.conversation_turn_checkpoints'::regclass;
 IF checkpoint_owner NOT IN ('everydayai','everydayai_owner') THEN RAISE EXCEPTION 'TASK_CHECKPOINT_OWNER_UNSUPPORTED'; END IF;
 EXECUTE format('SET LOCAL ROLE %I',checkpoint_owner);
 BEGIN
  ALTER TABLE public.conversation_turn_checkpoints ADD CONSTRAINT agent_task_direct_rollback_guard_checkpoint
    CHECK (state#>'{_task_bootstrap,input,model_directed}' IS DISTINCT FROM 'true'::jsonb
      AND state#>'{_task_execution,model_directed}' IS DISTINCT FROM 'true'::jsonb
      AND NOT state ? '_task_direct_plan' AND NOT state ? '_task_direct_seen');
 EXCEPTION WHEN check_violation THEN RAISE EXCEPTION 'TASK_DIRECT_COMPATIBLE_RUNTIME_REQUIRED'; END;
 SET LOCAL ROLE everydayai;
END $$;

DROP TRIGGER agent_task_execution_policy_guard ON public.agent_task_states;
DROP FUNCTION public.guard_agent_task_execution_policy();
DROP FUNCTION public.agent_task_method_extension_valid(jsonb,jsonb,jsonb);
ALTER FUNCTION public.agent_task_method_extension_valid_before371(jsonb,jsonb,jsonb) RENAME TO agent_task_method_extension_valid;
DROP FUNCTION public.agent_task_direct_extension_valid(jsonb,jsonb);
DROP FUNCTION public.agent_task_direct_records_valid(jsonb);
DROP FUNCTION public.agent_task_json_array_prefix(jsonb,jsonb);
ALTER TABLE public.agent_task_states DROP CONSTRAINT agent_task_direct_rollback_guard;
DO $$ DECLARE checkpoint_owner text; BEGIN
 SELECT pg_get_userbyid(relowner) INTO checkpoint_owner FROM pg_class WHERE oid='public.conversation_turn_checkpoints'::regclass;
 EXECUTE format('SET LOCAL ROLE %I',checkpoint_owner);
 ALTER TABLE public.conversation_turn_checkpoints DROP CONSTRAINT agent_task_direct_rollback_guard_checkpoint;
 SET LOCAL ROLE everydayai;
END $$;

