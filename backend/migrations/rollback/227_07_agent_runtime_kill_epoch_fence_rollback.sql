SET LOCAL ROLE everydayai_owner;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM agent_runtime_owner_fences WHERE status='active') THEN
        RAISE EXCEPTION 'AR_17_3_B_ROLLBACK_BLOCKED_ACTIVE_OWNER_FENCE' USING ERRCODE='55000';
    END IF;
END $$;
REVOKE EXECUTE ON FUNCTION runtime_submit_ingress_v4(UUID,UUID,UUID,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,JSONB,JSONB,TEXT,JSONB), claim_ready_agent_action_snapshots_v2(TEXT,TEXT,INTEGER,INTEGER), claim_ready_agent_actions_v2(TEXT,TEXT,INTEGER,INTEGER), recover_expired_agent_action_attempt_v2(UUID,BIGINT,TEXT,INTEGER), gate_agent_action_dispatch_v2(UUID,UUID,BIGINT,TEXT,UUID,TEXT,INTEGER,TEXT,TEXT), renew_agent_action_attempt_v2(UUID,UUID,BIGINT,INTEGER), mark_agent_action_dispatching_v2(UUID,UUID,BIGINT,TEXT), complete_agent_action_v2(UUID,UUID,BIGINT,TEXT,JSONB), fail_agent_action_v2(UUID,UUID,BIGINT,TEXT,JSONB), fail_claimed_agent_action_v2(UUID,UUID,BIGINT,TEXT,TEXT), mark_agent_action_accepted_v2(UUID,UUID,BIGINT,TEXT,JSONB), record_agent_action_unknown_v2(UUID,UUID,BIGINT,TEXT,JSONB), claim_agent_action_reconciliation_v2(UUID,BIGINT,TEXT,INTEGER), renew_agent_action_reconciliation_v2(UUID,UUID,BIGINT,INTEGER), resolve_agent_action_reconciliation_v2(UUID,UUID,BIGINT,TEXT,TEXT,JSONB,JSONB) FROM PUBLIC;
DROP FUNCTION runtime_submit_ingress_v4(UUID,UUID,UUID,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,JSONB,JSONB,TEXT,JSONB);
DROP FUNCTION claim_ready_agent_action_snapshots_v2(TEXT,TEXT,INTEGER,INTEGER);
DROP FUNCTION claim_ready_agent_actions_v2(TEXT,TEXT,INTEGER,INTEGER);
DROP FUNCTION recover_expired_agent_action_attempt_v2(UUID,BIGINT,TEXT,INTEGER);
DROP FUNCTION gate_agent_action_dispatch_v2(UUID,UUID,BIGINT,TEXT,UUID,TEXT,INTEGER,TEXT,TEXT);
DROP FUNCTION renew_agent_action_attempt_v2(UUID,UUID,BIGINT,INTEGER);
DROP FUNCTION mark_agent_action_dispatching_v2(UUID,UUID,BIGINT,TEXT);
DROP FUNCTION complete_agent_action_v2(UUID,UUID,BIGINT,TEXT,JSONB);
DROP FUNCTION fail_agent_action_v2(UUID,UUID,BIGINT,TEXT,JSONB);
DROP FUNCTION fail_claimed_agent_action_v2(UUID,UUID,BIGINT,TEXT,TEXT);
DROP FUNCTION mark_agent_action_accepted_v2(UUID,UUID,BIGINT,TEXT,JSONB);
DROP FUNCTION record_agent_action_unknown_v2(UUID,UUID,BIGINT,TEXT,JSONB);
DROP FUNCTION claim_agent_action_reconciliation_v2(UUID,BIGINT,TEXT,INTEGER);
DROP FUNCTION renew_agent_action_reconciliation_v2(UUID,UUID,BIGINT,INTEGER);
DROP FUNCTION resolve_agent_action_reconciliation_v2(UUID,UUID,BIGINT,TEXT,TEXT,JSONB,JSONB);
DROP FUNCTION _agent_runtime_record_attempt_fence(UUID);
DROP FUNCTION _agent_runtime_kill_epoch_context(UUID,UUID,TEXT,BIGINT,TEXT);
GRANT EXECUTE ON FUNCTION runtime_submit_ingress_v2(UUID,UUID,UUID,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,JSONB,JSONB,TEXT,JSONB), runtime_submit_ingress_v3(UUID,UUID,UUID,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,JSONB,JSONB,TEXT,JSONB) TO everydayai_runtime,everydayai_wecom_runtime;
ALTER FUNCTION enqueue_wecom_runtime_turn_v5(JSONB,UUID,UUID,UUID,JSONB,JSONB,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT)
    RENAME TO _enqueue_wecom_runtime_turn_v5_227_07;
ALTER FUNCTION _enqueue_wecom_runtime_turn_v5_227_01(JSONB,UUID,UUID,UUID,JSONB,JSONB,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT)
    RENAME TO enqueue_wecom_runtime_turn_v5;
DROP FUNCTION _enqueue_wecom_runtime_turn_v5_227_07(JSONB,UUID,UUID,UUID,JSONB,JSONB,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT);
GRANT EXECUTE ON FUNCTION enqueue_wecom_runtime_turn_v5(JSONB,UUID,UUID,UUID,JSONB,JSONB,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT) TO everydayai_wecom_runtime;
RESET ROLE;
