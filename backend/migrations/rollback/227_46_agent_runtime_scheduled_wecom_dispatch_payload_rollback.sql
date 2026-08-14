-- Roll back 227_46; this migration creates no persistent business facts.
SET LOCAL ROLE everydayai_owner;

REVOKE ALL ON FUNCTION read_agent_runtime_scheduled_wecom_dispatch_payload_v1(
 UUID,UUID,UUID,UUID,TEXT,BIGINT,BIGINT) FROM everydayai_wecom_runtime;
DROP FUNCTION read_agent_runtime_scheduled_wecom_dispatch_payload_v1(
 UUID,UUID,UUID,UUID,TEXT,BIGINT,BIGINT);

RESET ROLE;
