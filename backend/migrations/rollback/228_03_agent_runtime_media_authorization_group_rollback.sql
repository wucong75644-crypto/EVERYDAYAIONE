-- Roll back 228.03 only when no grouped authorization fact exists.
SET LOCAL ROLE everydayai_owner;

DO $guard$
BEGIN
 IF EXISTS(SELECT 1 FROM agent_interactions
   WHERE confirmation_group_hash IS NOT NULL
      OR confirmation_group_leader_id IS NOT NULL) THEN
  RAISE EXCEPTION 'AGENT_MEDIA_AUTHORIZATION_GROUP_ROLLBACK_HAS_FACTS'
   USING ERRCODE='55000';
 END IF;
END
$guard$;

DROP FUNCTION complete_model_attempt_with_raw_actions(
 UUID,UUID,BIGINT,BIGINT,TEXT,JSONB,TEXT,TEXT,JSONB,INTEGER,JSONB);
ALTER FUNCTION _complete_model_attempt_with_raw_actions_228_01(
 UUID,UUID,BIGINT,BIGINT,TEXT,JSONB,TEXT,TEXT,JSONB,INTEGER,JSONB)
 RENAME TO complete_model_attempt_with_raw_actions;

DROP FUNCTION resolve_agent_tool_confirmation_v3(
 TEXT,UUID,UUID,BIGINT,UUID,UUID,TEXT,TIMESTAMPTZ,BOOLEAN);
ALTER FUNCTION _resolve_agent_tool_confirmation_v3_223(
 TEXT,UUID,UUID,BIGINT,UUID,UUID,TEXT,TIMESTAMPTZ,BOOLEAN)
 RENAME TO resolve_agent_tool_confirmation_v3;

DROP FUNCTION resolve_agent_authorization_interaction(
 UUID,BIGINT,TEXT,TEXT,JSONB,TEXT,TEXT,INTEGER);
ALTER FUNCTION _resolve_agent_authorization_interaction_220_25(
 UUID,BIGINT,TEXT,TEXT,JSONB,TEXT,TEXT,INTEGER)
 RENAME TO resolve_agent_authorization_interaction;

DROP FUNCTION claim_agent_tool_confirmation_notification(TEXT,INTEGER);
ALTER FUNCTION _claim_agent_tool_confirmation_notification_223(TEXT,INTEGER)
 RENAME TO claim_agent_tool_confirmation_notification;

DROP FUNCTION resolve_agent_tool_batch_confirmation_v1(
 TEXT,UUID,UUID,BIGINT,UUID,UUID,TEXT,TEXT,TIMESTAMPTZ,BOOLEAN);
DROP FUNCTION claim_agent_tool_batch_confirmation_v1(TEXT,INTEGER);
DROP FUNCTION open_agent_authorization_batch_v1(UUID,TEXT,JSONB,INTEGER);
DROP FUNCTION _expire_agent_media_authorization_group_v1(TEXT);
DROP FUNCTION _agent_media_authorization_group_hash_v1(
 UUID,TEXT,UUID,UUID,UUID,UUID,JSONB);

DROP INDEX uq_agent_interactions_confirmation_group_leader;
DROP INDEX idx_agent_interactions_confirmation_group;
ALTER TABLE agent_interactions
 DROP CONSTRAINT agent_interactions_confirmation_group_pair,
 DROP COLUMN confirmation_group_leader_id,
 DROP COLUMN confirmation_group_hash;

GRANT EXECUTE ON FUNCTION complete_model_attempt_with_raw_actions(
 UUID,UUID,BIGINT,BIGINT,TEXT,JSONB,TEXT,TEXT,JSONB,INTEGER,JSONB)
TO everydayai_agent_runtime_worker;
GRANT EXECUTE ON FUNCTION
 claim_agent_tool_confirmation_notification(TEXT,INTEGER)
TO everydayai_projection_worker;
GRANT EXECUTE ON FUNCTION
 resolve_agent_authorization_interaction(
  UUID,BIGINT,TEXT,TEXT,JSONB,TEXT,TEXT,INTEGER),
 resolve_agent_tool_confirmation_v3(
  TEXT,UUID,UUID,BIGINT,UUID,UUID,TEXT,TIMESTAMPTZ,BOOLEAN)
TO everydayai_runtime,everydayai_wecom_runtime;

RESET ROLE;
