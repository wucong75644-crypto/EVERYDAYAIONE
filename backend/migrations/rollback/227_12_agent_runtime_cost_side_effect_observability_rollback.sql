SET LOCAL ROLE everydayai_owner;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM agent_action_cost_settlements)
       OR EXISTS (SELECT 1 FROM agent_runtime_provider_submission_facts) THEN
        RAISE EXCEPTION 'AR_17_6_ROLLBACK_BLOCKED_LEDGER_FACTS' USING ERRCODE='55000';
    END IF;
END $$;
DROP FUNCTION get_agent_runtime_cost_side_effect_snapshot(UUID,TEXT,TEXT,TEXT,INTEGER);
RESET ROLE;
