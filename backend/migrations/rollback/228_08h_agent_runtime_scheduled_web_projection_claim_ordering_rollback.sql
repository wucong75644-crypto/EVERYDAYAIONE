-- Roll back 228.08h only after all scheduled Web projection work is drained.

SET LOCAL ROLE everydayai_owner;

DO $guard$
BEGIN
 IF to_regprocedure(
  '_claim_agent_runtime_scheduled_web_projection_227_36_v1(text,uuid,integer)'
 ) IS NULL
 OR to_regprocedure(
  'claim_agent_runtime_scheduled_web_projection_v1(text,uuid,integer)'
 ) IS NULL
 OR obj_description(
  'claim_agent_runtime_scheduled_web_projection_v1(text,uuid,integer)'::REGPROCEDURE,
  'pg_proc'
 ) IS DISTINCT FROM
  '228_08h ordered scheduled Web projection receipt materialization' THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_228_08H_ROLLBACK_DEPENDENCY_CONFLICT'
   USING ERRCODE='55000';
 END IF;
 IF EXISTS(
  SELECT 1 FROM agent_runtime_scheduled_web_projection_receipts
  WHERE projection_state IN('pending','claimed','projected')
 ) OR EXISTS(
  SELECT 1 FROM agent_runtime_scheduled_delivery_intents intent
  JOIN agent_runtime_scheduled_delivery_targets target
   ON(target.scheduled_run_id,target.target_key,target.target_hash)=
     (intent.scheduled_run_id,intent.target_key,intent.target_hash)
  WHERE target.target_type='web'
  AND NOT EXISTS(
   SELECT 1 FROM agent_runtime_scheduled_web_projection_receipts receipt
   WHERE receipt.intent_id=intent.id
  )
 ) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_228_08H_ROLLBACK_ACTIVE_PROJECTION'
   USING ERRCODE='55000';
 END IF;
END
$guard$;

REVOKE ALL ON FUNCTION
 claim_agent_runtime_scheduled_web_projection_v1(TEXT,UUID,INTEGER)
 FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,
 everydayai_sync,everydayai,everydayai_agent_runtime_worker,
 everydayai_projection_worker,everydayai_authorization_worker,
 everydayai_sandbox_worker,everydayai_runtime_admin;
DROP FUNCTION claim_agent_runtime_scheduled_web_projection_v1(TEXT,UUID,INTEGER);
ALTER FUNCTION _claim_agent_runtime_scheduled_web_projection_227_36_v1(
 TEXT,UUID,INTEGER
) RENAME TO claim_agent_runtime_scheduled_web_projection_v1;
REVOKE ALL ON FUNCTION
 claim_agent_runtime_scheduled_web_projection_v1(TEXT,UUID,INTEGER)
 FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,
 everydayai_sync,everydayai,everydayai_agent_runtime_worker,
 everydayai_projection_worker,everydayai_authorization_worker,
 everydayai_sandbox_worker,everydayai_runtime_admin;
GRANT EXECUTE ON FUNCTION
 claim_agent_runtime_scheduled_web_projection_v1(TEXT,UUID,INTEGER)
 TO everydayai_projection_worker;

RESET ROLE;
