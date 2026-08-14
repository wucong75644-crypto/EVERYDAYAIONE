-- Restore pre-B6 Child Run lifecycle only when no recursive cancel fact remains.
SET LOCAL ROLE everydayai_owner;

DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM agent_runtime_child_run_cancel_intents) OR EXISTS(
  SELECT 1 FROM agent_runs parent
  JOIN agent_actions action ON action.run_id=parent.id
  LEFT JOIN agent_runs child ON child.parent_action_id=action.id
  WHERE parent.status='cancelled'
   AND action.tool_name IN('image_agent','erp_agent','erp_analyze')
   AND (action.status IN('running','accepted','unknown')
        OR child.status NOT IN('completed','failed','cancelled'))
 ) THEN
  RAISE EXCEPTION 'AGENT_CHILD_CANCEL_ROLLBACK_PENDING_FACTS' USING ERRCODE='55000';
 END IF;
END $$;

CREATE OR REPLACE FUNCTION cancel_agent_run(
 p_run_id UUID,p_expected_state_version BIGINT,p_reason TEXT)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE v_run agent_runs%ROWTYPE; v_session_id UUID;
 v_interaction agent_interactions%ROWTYPE; v_result JSONB;
BEGIN
 IF session_user='everydayai_worker' THEN PERFORM _assert_agent_runtime_actor(TRUE);
 ELSE PERFORM _assert_agent_runtime_actor(FALSE); END IF;
 SELECT session_id INTO v_session_id FROM agent_runs WHERE id=p_run_id;
 PERFORM 1 FROM agent_runtime_sessions WHERE id=v_session_id FOR UPDATE;
 SELECT * INTO v_run FROM agent_runs WHERE id=p_run_id FOR UPDATE;
 PERFORM _lock_agent_model_gateway_cancel_scope_v1(p_run_id);
 PERFORM 1 FROM agent_actions WHERE run_id=p_run_id ORDER BY id FOR UPDATE;
 PERFORM 1 FROM agent_action_attempts attempt JOIN agent_actions action
  ON action.id=attempt.action_id WHERE action.run_id=p_run_id
  ORDER BY attempt.id FOR UPDATE OF attempt;
 PERFORM 1 FROM agent_interactions WHERE run_id=p_run_id ORDER BY id FOR UPDATE;
 PERFORM 1 FROM agent_authorization_grants WHERE run_id=p_run_id ORDER BY id FOR UPDATE;
 IF v_run.status NOT IN('completed','failed','cancelled')
 AND v_run.state_version=p_expected_state_version THEN
  FOR v_interaction IN UPDATE agent_interactions SET status='cancelled',
   resolved_at=clock_timestamp(),recovery_worker_id=NULL,recovery_token=NULL,
   recovery_lease_expires_at=NULL,state_version=state_version+1,
   updated_at=clock_timestamp() WHERE run_id=p_run_id AND status='open' RETURNING * LOOP
   PERFORM append_agent_runtime_event(v_interaction.session_id,'interaction.cancelled',
    v_interaction.run_id,NULL,v_interaction.id,'system',session_user,
    jsonb_build_object('interaction_id',v_interaction.id,'action_id',v_interaction.action_id,
     'reason',p_reason),ARRAY['web_runtime','audit']::TEXT[]);
  END LOOP;
  UPDATE agent_authorization_grants SET status='revoked',revoked_at=clock_timestamp()
   WHERE run_id=p_run_id AND status='active';
 END IF;
 v_result:=_cancel_agent_run_220_23(p_run_id,p_expected_state_version,p_reason);
 RETURN v_result;
END $$;

REVOKE ALL ON FUNCTION
 create_agent_child_run_strict_v2(UUID,UUID,TEXT,UUID,INTEGER,TEXT,JSONB),
 read_agent_child_run_binding_v3(UUID,UUID,UUID,UUID,TEXT,UUID,BIGINT),
 aggregate_agent_child_run_strict_v2(UUID,UUID,UUID,TEXT,UUID,UUID,INTEGER,INTEGER,JSONB),
 claim_next_agent_child_run_cancel_intent_v1(TEXT,INTEGER),
 get_claimed_agent_child_run_cancel_intent_v1(TEXT),
 apply_agent_child_run_cancel_intent_v1(UUID,UUID,BIGINT,TEXT),
 read_agent_child_run_cancel_intent_v1(UUID,UUID,UUID,BIGINT,TEXT),
 finalize_agent_action_child_cancel_v1(UUID,UUID,BIGINT,TEXT,UUID,TEXT,BIGINT)
FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,
 everydayai_sync,everydayai,everydayai_agent_runtime_worker,
 everydayai_agent_model_gateway,everydayai_projection_worker,
 everydayai_authorization_worker,everydayai_sandbox_worker;

DROP FUNCTION finalize_agent_action_child_cancel_v1(UUID,UUID,BIGINT,TEXT,UUID,TEXT,BIGINT);
DROP FUNCTION read_agent_child_run_cancel_intent_v1(UUID,UUID,UUID,BIGINT,TEXT);
DROP FUNCTION apply_agent_child_run_cancel_intent_v1(UUID,UUID,BIGINT,TEXT);
DROP FUNCTION _cancel_child_run_from_intent_v1(UUID,TEXT);
DROP FUNCTION get_claimed_agent_child_run_cancel_intent_v1(TEXT);
DROP FUNCTION claim_next_agent_child_run_cancel_intent_v1(TEXT,INTEGER);
DROP FUNCTION aggregate_agent_child_run_strict_v2(UUID,UUID,UUID,TEXT,UUID,UUID,INTEGER,INTEGER,JSONB);
DROP FUNCTION read_agent_child_run_binding_v3(UUID,UUID,UUID,UUID,TEXT,UUID,BIGINT);
DROP FUNCTION create_agent_child_run_strict_v2(UUID,UUID,TEXT,UUID,INTEGER,TEXT,JSONB);
DROP FUNCTION _seed_agent_child_cancel_intents_v1(UUID);
DROP FUNCTION _agent_child_cancel_proof_v1(agent_runtime_child_run_cancel_intents,agent_runs,TEXT);
DROP TRIGGER agent_child_cancel_intent_immutable ON agent_runtime_child_run_cancel_intents;
DROP FUNCTION _agent_child_cancel_intent_immutable_v1();
DROP TABLE agent_runtime_child_run_cancel_intents;

GRANT EXECUTE ON FUNCTION
 create_agent_child_run_strict(UUID,UUID,TEXT,UUID,INTEGER,TEXT,JSONB),
 read_agent_child_run_strict_v2(UUID,UUID,UUID,UUID,TEXT,UUID,INTEGER,INTEGER),
 aggregate_agent_child_run_strict(UUID,UUID,UUID,TEXT,UUID,UUID,INTEGER,INTEGER,JSONB),
 cancel_agent_child_run_strict_v2(UUID,UUID,UUID,TEXT,UUID,UUID,INTEGER,TEXT)
TO everydayai_agent_runtime_worker;

RESET ROLE;
