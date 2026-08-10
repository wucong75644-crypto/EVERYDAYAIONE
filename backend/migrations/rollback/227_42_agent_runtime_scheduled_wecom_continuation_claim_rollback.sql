-- Roll back 227_42 only while no continuation claim fact exists.

SET LOCAL ROLE everydayai_owner;

LOCK TABLE agent_runtime_scheduled_wecom_continuation_claim_requests,
 agent_runtime_scheduled_wecom_deliveries IN SHARE ROW EXCLUSIVE MODE;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_continuation_claim_requests) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_CONTINUATION_ROLLBACK_HAS_FACTS'
   USING ERRCODE='55000';
 END IF;
END $$;

REVOKE ALL ON FUNCTION claim_agent_runtime_scheduled_wecom_delivery_v2(UUID,TEXT,INTEGER)
 FROM everydayai_wecom_runtime;
DROP FUNCTION claim_agent_runtime_scheduled_wecom_delivery_v2(UUID,TEXT,INTEGER);
DROP FUNCTION _agent_runtime_scheduled_wecom_continuation_json(
 agent_runtime_scheduled_wecom_continuation_claim_requests,TEXT);
DROP FUNCTION _agent_runtime_scheduled_wecom_terminalize_unavailable_continuation(UUID,TEXT);
DROP TRIGGER runtime_scheduled_wecom_continuation_global_request_guard
 ON agent_runtime_scheduled_wecom_continuation_claim_requests;
DROP FUNCTION _agent_runtime_scheduled_wecom_continuation_request_guard();
DROP TRIGGER runtime_scheduled_wecom_continuation_claim_immutable
 ON agent_runtime_scheduled_wecom_continuation_claim_requests;
DROP FUNCTION _agent_runtime_scheduled_wecom_continuation_claim_immutable();
DROP TABLE agent_runtime_scheduled_wecom_continuation_claim_requests;

CREATE OR REPLACE FUNCTION _agent_runtime_scheduled_wecom_reconcile_request_guard() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
 PERFORM _agent_runtime_scheduled_wecom_global_request_lock(NEW.request_id);
 IF EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_deliveries
   WHERE claim_request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_dispatch_attempts
   WHERE claim_request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_prepared_recovery_requests
   WHERE request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_outcome_requests
   WHERE request_id=NEW.request_id) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_RECONCILE_REQUEST_CONFLICT'
   USING ERRCODE='55000';
 END IF;
 RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION _agent_runtime_scheduled_wecom_legacy_request_guard() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE guard_request_id UUID;
BEGIN
 IF TG_TABLE_NAME='agent_runtime_scheduled_wecom_deliveries' THEN
  IF NEW.claim_request_id IS NULL
  OR NEW.claim_request_id IS NOT DISTINCT FROM OLD.claim_request_id THEN RETURN NEW; END IF;
  guard_request_id:=NEW.claim_request_id;
 ELSE guard_request_id:=NEW.request_id;
 END IF;
 PERFORM _agent_runtime_scheduled_wecom_global_request_lock(guard_request_id);
 IF EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_reconcile_claim_requests
  WHERE request_id=guard_request_id) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_RECONCILE_REQUEST_CONFLICT'
   USING ERRCODE='55000';
 END IF;
 RETURN NEW;
END $$;

GRANT EXECUTE ON FUNCTION claim_agent_runtime_scheduled_wecom_delivery_v1(UUID,TEXT,INTEGER)
 TO everydayai_wecom_runtime;

RESET ROLE;
