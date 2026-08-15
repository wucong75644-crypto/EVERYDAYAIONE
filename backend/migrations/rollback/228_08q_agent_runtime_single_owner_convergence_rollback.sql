-- Roll back production callers to the prior contracts.  The additive
-- attempt_id receipt column and its indexes remain so audit facts are retained.
SET LOCAL ROLE everydayai_owner;

REVOKE EXECUTE ON FUNCTION
 submit_runtime_ingress_required_v1(UUID,UUID,UUID,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,JSONB,JSONB,TEXT,JSONB,UUID,TEXT,UUID,UUID,UUID,TEXT)
FROM everydayai_runtime;
REVOKE EXECUTE ON FUNCTION
 enqueue_wecom_runtime_turn_required_v1(JSONB,UUID,UUID,UUID,JSONB,JSONB,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT)
FROM everydayai_wecom_runtime;
REVOKE EXECUTE ON FUNCTION
 gate_agent_action_dispatch_final_v1(UUID,UUID,BIGINT,TEXT,UUID,TEXT,INTEGER,TEXT,TEXT),
 claim_agent_action_dispatch_final_v1(TEXT,TEXT,INTEGER,INTEGER)
FROM everydayai_agent_runtime_worker;

DROP FUNCTION claim_agent_action_dispatch_final_v1(TEXT,TEXT,INTEGER,INTEGER);
DROP FUNCTION _recover_expired_agent_action_claims_v1(TEXT,INTEGER);
DROP FUNCTION gate_agent_action_dispatch_final_v1(
 UUID,UUID,BIGINT,TEXT,UUID,TEXT,INTEGER,TEXT,TEXT);
DROP FUNCTION _record_safe_attempt_policy_receipt_v1(
 UUID,UUID,BIGINT,TEXT,TEXT,INTEGER,TEXT);
DROP FUNCTION enqueue_wecom_runtime_turn_required_v1(
 JSONB,UUID,UUID,UUID,JSONB,JSONB,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT);
DROP FUNCTION submit_runtime_ingress_required_v1(
 UUID,UUID,UUID,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,UUID,TEXT,TEXT,
 TEXT,JSONB,JSONB,TEXT,JSONB,UUID,TEXT,UUID,UUID,UUID,TEXT);
DROP FUNCTION _submit_runtime_ingress_core_v1(
 UUID,UUID,UUID,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,UUID,TEXT,TEXT,
 TEXT,JSONB,JSONB,TEXT,JSONB);

GRANT EXECUTE ON FUNCTION
 get_agent_runtime_ingress_capability(),
 runtime_submit_ingress_v4(UUID,UUID,UUID,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,JSONB,JSONB,TEXT,JSONB),
 runtime_submit_ingress_v6_required(UUID,UUID,UUID,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,JSONB,JSONB,TEXT,JSONB,UUID,TEXT,UUID,UUID,UUID,TEXT),
 runtime_submit_ingress_v5_owner_transition(UUID,UUID,UUID,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,JSONB,JSONB,TEXT,JSONB,UUID,TEXT,UUID,UUID,UUID,TEXT)
TO everydayai_runtime;
GRANT EXECUTE ON FUNCTION
 get_agent_runtime_ingress_capability(),
 enqueue_wecom_runtime_turn_v3(JSONB,UUID,UUID,UUID,JSONB,JSONB,TEXT,TEXT,TEXT),
 enqueue_wecom_runtime_turn_v4(JSONB,UUID,UUID,UUID,JSONB,JSONB,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT),
 enqueue_wecom_runtime_turn_v5(JSONB,UUID,UUID,UUID,JSONB,JSONB,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT),
 enqueue_wecom_runtime_turn_v6(JSONB,UUID,UUID,UUID,JSONB,JSONB,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT)
TO everydayai_wecom_runtime;
GRANT EXECUTE ON FUNCTION
 claim_ready_agent_action_snapshots_v2(TEXT,TEXT,INTEGER,INTEGER),
 claim_ready_agent_actions_v2(TEXT,TEXT,INTEGER,INTEGER),
 gate_agent_action_dispatch_v2(UUID,UUID,BIGINT,TEXT,UUID,TEXT,INTEGER,TEXT,TEXT),
 activate_agent_safe_action(UUID,UUID,BIGINT,TEXT,TEXT,INTEGER,TEXT)
TO everydayai_agent_runtime_worker;
GRANT EXECUTE ON FUNCTION
 activate_agent_safe_action(UUID,UUID,BIGINT,TEXT,TEXT,INTEGER,TEXT)
TO everydayai_authorization_worker;
GRANT EXECUTE ON FUNCTION
 set_agent_runtime_org_rollout(UUID,UUID,BOOLEAN,TEXT),
 set_agent_runtime_rollout_subject(TEXT,TEXT,TEXT,BOOLEAN,JSONB)
TO everydayai_runtime_admin;

RESET ROLE;
