SET LOCAL ROLE everydayai_owner;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM agent_actions LIMIT 1)
       OR EXISTS (SELECT 1 FROM agent_action_attempts LIMIT 1)
       OR EXISTS (SELECT 1 FROM agent_action_results LIMIT 1)
       OR EXISTS (SELECT 1 FROM agent_action_claim_batches LIMIT 1) THEN
        RAISE EXCEPTION 'AGENT_ACTION_ROLLBACK_HAS_FACTS' USING ERRCODE = '55000';
    END IF;
END;
$$;

DROP FUNCTION _agent_action_json_is_safe(JSONB);
DROP TABLE agent_action_results;
DROP TABLE agent_action_attempts;
DROP TABLE agent_action_claim_batches;
DROP TABLE agent_actions;

RESET ROLE;
