SET LOCAL ROLE everydayai_owner;

DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM agent_runtime_scheduled_run_credit_budgets)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_model_credit_allocations)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_credit_overages) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_BUDGET_ROLLBACK_FACTS_EXIST' USING ERRCODE='55000';
 END IF;
END $$;

REVOKE ALL ON FUNCTION prepare_model_attempt(UUID,UUID,BIGINT,TEXT,TEXT,TEXT,TEXT,JSONB,INTEGER,INTEGER)
 FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,everydayai_sync,everydayai,
 everydayai_agent_runtime_worker;
DROP FUNCTION prepare_model_attempt(UUID,UUID,BIGINT,TEXT,TEXT,TEXT,TEXT,JSONB,INTEGER,INTEGER);
DROP FUNCTION _settle_agent_model_credits(agent_model_steps,UUID,TEXT,INTEGER);
DROP FUNCTION _release_agent_model_credits(UUID);
DROP FUNCTION _adjust_model_attempt_credits(UUID,TEXT,INTEGER);
ALTER FUNCTION _prepare_model_attempt_without_scheduled_budget_v1(UUID,UUID,BIGINT,TEXT,TEXT,TEXT,TEXT,JSONB,INTEGER,INTEGER)
 RENAME TO prepare_model_attempt;
ALTER FUNCTION _settle_agent_model_credits_without_scheduled_budget_v1(agent_model_steps,UUID,TEXT,INTEGER)
 RENAME TO _settle_agent_model_credits;
ALTER FUNCTION _release_agent_model_credits_without_scheduled_budget_v1(UUID)
 RENAME TO _release_agent_model_credits;
ALTER FUNCTION _adjust_model_attempt_credits_without_scheduled_budget_v1(UUID,TEXT,INTEGER)
 RENAME TO _adjust_model_attempt_credits;
DROP TRIGGER runtime_scheduled_model_credit_projection ON agent_model_credit_settlements;
DROP TRIGGER runtime_scheduled_credit_overage_guard ON agent_runtime_scheduled_credit_overages;
DROP TRIGGER runtime_scheduled_model_credit_allocation_guard ON agent_runtime_scheduled_model_credit_allocations;
DROP TRIGGER runtime_scheduled_run_credit_budget_guard ON agent_runtime_scheduled_run_credit_budgets;
DROP FUNCTION _agent_runtime_scheduled_budget_project_settlement();
DROP FUNCTION _agent_runtime_scheduled_budget_fact_guard();
DROP TABLE agent_runtime_scheduled_credit_overages;
DROP TABLE agent_runtime_scheduled_model_credit_allocations;
DROP TABLE agent_runtime_scheduled_run_credit_budgets;
GRANT EXECUTE ON FUNCTION prepare_model_attempt(UUID,UUID,BIGINT,TEXT,TEXT,TEXT,TEXT,JSONB,INTEGER,INTEGER)
 TO everydayai_agent_runtime_worker;

RESET ROLE;
