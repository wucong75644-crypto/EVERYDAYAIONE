-- Roll back 227_45; this migration creates no persistent business facts.
SET LOCAL ROLE everydayai_owner;

REVOKE ALL ON FUNCTION
 prepare_agent_runtime_scheduled_wecom_dispatch_v2(
  UUID,UUID,UUID,UUID,TEXT,BIGINT,BIGINT,TEXT,TEXT,BIGINT),
 start_agent_runtime_scheduled_wecom_dispatch_v2(
  UUID,UUID,UUID,UUID,UUID,TEXT,BIGINT,BIGINT,TEXT,TEXT,BIGINT),
 read_agent_runtime_scheduled_wecom_dispatch_attempt_v2(
  UUID,UUID,UUID,UUID,UUID,TEXT,TEXT,TEXT,BIGINT)
 FROM everydayai_wecom_runtime;

DROP FUNCTION read_agent_runtime_scheduled_wecom_dispatch_attempt_v2(
 UUID,UUID,UUID,UUID,UUID,TEXT,TEXT,TEXT,BIGINT);
DROP FUNCTION start_agent_runtime_scheduled_wecom_dispatch_v2(
 UUID,UUID,UUID,UUID,UUID,TEXT,BIGINT,BIGINT,TEXT,TEXT,BIGINT);
DROP FUNCTION prepare_agent_runtime_scheduled_wecom_dispatch_v2(
 UUID,UUID,UUID,UUID,TEXT,BIGINT,BIGINT,TEXT,TEXT,BIGINT);
DROP FUNCTION _agent_runtime_scheduled_wecom_dispatch_versioned_json(
 JSONB,UUID,UUID,UUID,UUID,UUID,TEXT,TEXT,TEXT,BIGINT);

GRANT EXECUTE ON FUNCTION
 prepare_agent_runtime_scheduled_wecom_dispatch_v1(
  UUID,UUID,UUID,UUID,TEXT,BIGINT,BIGINT,TEXT,TEXT,BIGINT),
 start_agent_runtime_scheduled_wecom_dispatch_v1(
  UUID,UUID,UUID,UUID,UUID,TEXT,BIGINT,BIGINT,TEXT,TEXT,BIGINT),
 read_agent_runtime_scheduled_wecom_dispatch_attempt_v1(
  UUID,UUID,UUID,UUID,UUID,TEXT,TEXT,TEXT,BIGINT)
 TO everydayai_wecom_runtime;

RESET ROLE;
