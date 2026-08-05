SET LOCAL ROLE everydayai_owner;

DO $$
BEGIN
  IF EXISTS(SELECT 1 FROM agent_safe_action_activations) THEN
    RAISE EXCEPTION 'AGENT_SAFE_ACTION_ACTIVATION_FACTS_EXIST';
  END IF;
END $$;

REVOKE ALL ON FUNCTION
 activate_agent_safe_action(UUID,UUID,BIGINT,TEXT,TEXT,INTEGER,TEXT)
FROM PUBLIC,everydayai_agent_runtime_worker,everydayai_authorization_worker,
 everydayai_projection_worker,everydayai_sandbox_worker,everydayai_worker;
DROP FUNCTION activate_agent_safe_action(UUID,UUID,BIGINT,TEXT,TEXT,INTEGER,TEXT);
DROP TRIGGER trg_agent_safe_activation_immutable ON agent_safe_action_activations;
DROP FUNCTION _agent_safe_activation_immutable();
DROP TABLE agent_safe_action_activations;

RESET ROLE;
