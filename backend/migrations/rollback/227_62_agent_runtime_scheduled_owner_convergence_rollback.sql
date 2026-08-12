-- 227_62 rollback: only while the convergence control is still pending.

SET LOCAL ROLE everydayai_owner;
DO $$
BEGIN
    IF EXISTS(
        SELECT 1 FROM agent_runtime_scheduled_adoption_control
        WHERE singleton AND state <> 'pending'
    ) THEN
        RAISE EXCEPTION 'SCHEDULED_OWNER_CONVERGENCE_ALREADY_COMPLETED';
    END IF;
END;
$$;

-- Restore the pre-227_62 worker behavior before removing the cutover helper.
CREATE OR REPLACE FUNCTION worker_claim_due_scheduled_executions_v1(
    p_now TIMESTAMPTZ, p_limit INTEGER
) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE t scheduled_tasks%ROWTYPE;items JSONB:='[]'::JSONB;item JSONB;claimed INTEGER:=0;
BEGIN
 PERFORM _agent_runtime_scheduled_submission_worker();
 IF p_now IS NULL OR p_limit NOT BETWEEN 1 AND 100 THEN RAISE EXCEPTION 'SCHEDULED_WORKER_CLAIM_ARGUMENT_INVALID' USING ERRCODE='22023'; END IF;
 FOR t IN SELECT candidate.* FROM scheduled_tasks candidate WHERE candidate.status='active'
  AND candidate.next_run_at IS NOT NULL AND candidate.next_run_at<=p_now
  AND(_agent_runtime_scheduled_submission_enabled() OR NOT EXISTS(
   SELECT 1 FROM agent_runtime_scheduled_execution_profiles profile
   WHERE profile.scheduled_task_id=candidate.id))
  AND(candidate.org_id IS NULL OR EXISTS(SELECT 1 FROM organizations o WHERE o.id=candidate.org_id AND o.status='active'))
  ORDER BY candidate.next_run_at,candidate.id LIMIT p_limit*4 FOR UPDATE OF candidate SKIP LOCKED LOOP
  EXIT WHEN claimed>=p_limit;
  IF EXISTS(SELECT 1 FROM agent_runtime_scheduled_execution_profiles e WHERE e.scheduled_task_id=t.id) THEN
   item:=_submit_agent_runtime_scheduled_execution_v1(t.id,'scheduled',
    'scheduled:'||t.next_run_at::TEXT,t.next_run_at,NULL,t.user_id,p_now);
   IF item->>'outcome'='runtime_disabled' THEN CONTINUE; END IF;
  ELSE
   UPDATE scheduled_tasks SET status='running',next_run_at=NULL,updated_at=p_now WHERE id=t.id RETURNING * INTO t;
   item:=jsonb_build_object('outcome','claimed','owner_kind','legacy','task',to_jsonb(t));
  END IF;
  items:=items||jsonb_build_array(item);claimed:=claimed+1;
 END LOOP; RETURN items;
END $$;

CREATE OR REPLACE FUNCTION worker_assert_scheduled_task_legacy_owner_v1(
    p_task_id UUID
) RETURNS JSONB
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
 PERFORM _agent_runtime_scheduled_submission_worker();
 IF p_task_id IS NULL THEN RAISE EXCEPTION 'SCHEDULED_LEGACY_OWNER_ARGUMENT_INVALID' USING ERRCODE='22023'; END IF;
 IF EXISTS(SELECT 1 FROM agent_runtime_scheduled_execution_profiles WHERE scheduled_task_id=p_task_id) THEN
  RAISE EXCEPTION 'SCHEDULED_RUN_RUNTIME_OWNED' USING ERRCODE='42501';
 END IF;
 RETURN jsonb_build_object('outcome','allowed','owner_kind','legacy');
END $$;

GRANT EXECUTE ON FUNCTION worker_claim_due_scheduled_executions_v1(TIMESTAMPTZ,INTEGER),
    worker_assert_scheduled_task_legacy_owner_v1(UUID) TO everydayai_worker;

DROP FUNCTION IF EXISTS complete_agent_runtime_scheduled_adoption_v1(UUID);
DROP FUNCTION IF EXISTS read_agent_runtime_scheduled_adoption_control_v1();
DROP FUNCTION IF EXISTS _agent_runtime_scheduled_adoption_complete();
DROP TABLE IF EXISTS agent_runtime_scheduled_adoption_control;
RESET ROLE;
