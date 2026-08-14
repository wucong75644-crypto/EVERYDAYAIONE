SET LOCAL ROLE everydayai_owner;
DO $$ BEGIN IF EXISTS(SELECT 1 FROM agent_action_cost_settlements) THEN RAISE EXCEPTION 'AGENT_RUNTIME_226_ROLLBACK_GUARD_FACTS_EXIST'; END IF; END $$;
DROP FUNCTION adjust_agent_action_cost(UUID,UUID,BIGINT,TEXT,TEXT); DROP FUNCTION refund_agent_action_cost(UUID,UUID,TEXT); DROP FUNCTION release_agent_action_cost(UUID,UUID,TEXT); DROP FUNCTION settle_agent_action_cost(UUID,UUID,BIGINT,TEXT,TEXT); DROP FUNCTION reserve_agent_action_cost(UUID,UUID,BIGINT,TEXT); DROP FUNCTION _record_agent_action_cost(UUID,UUID,TEXT,BIGINT,BIGINT,TEXT,TEXT); DROP TABLE agent_action_cost_settlements;
RESET ROLE;
