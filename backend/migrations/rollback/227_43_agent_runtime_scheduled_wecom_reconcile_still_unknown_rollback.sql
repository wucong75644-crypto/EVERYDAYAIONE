-- Roll back 227_43 only while no reconciliation result fact exists.

SET LOCAL ROLE everydayai_owner;

LOCK TABLE agent_runtime_scheduled_wecom_reconcile_result_requests IN SHARE ROW EXCLUSIVE MODE;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_reconcile_result_requests) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_RECONCILE_RESULT_ROLLBACK_HAS_FACTS'
   USING ERRCODE='55000';
 END IF;
END $$;

REVOKE ALL ON FUNCTION record_agent_runtime_scheduled_wecom_reconcile_result_v1(
 UUID,UUID,UUID,UUID,UUID,UUID,TEXT,BIGINT,BIGINT,TEXT,TEXT,BIGINT,TEXT,TEXT,TEXT,
 TEXT,JSONB,INTEGER) FROM everydayai_wecom_runtime;
DROP FUNCTION record_agent_runtime_scheduled_wecom_reconcile_result_v1(
 UUID,UUID,UUID,UUID,UUID,UUID,TEXT,BIGINT,BIGINT,TEXT,TEXT,BIGINT,TEXT,TEXT,TEXT,
 TEXT,JSONB,INTEGER);
DROP FUNCTION _agent_runtime_scheduled_wecom_reconcile_result_json(
 agent_runtime_scheduled_wecom_reconcile_result_requests,TEXT);
DROP TRIGGER runtime_scheduled_wecom_reconcile_result_global_request_guard
 ON agent_runtime_scheduled_wecom_reconcile_result_requests;
DROP FUNCTION _agent_runtime_scheduled_wecom_reconcile_result_request_guard();
DROP TRIGGER runtime_scheduled_wecom_reconcile_result_immutable
 ON agent_runtime_scheduled_wecom_reconcile_result_requests;
DROP FUNCTION _agent_runtime_scheduled_wecom_reconcile_result_immutable();
DROP TABLE agent_runtime_scheduled_wecom_reconcile_result_requests;
DROP FUNCTION _agent_runtime_scheduled_wecom_reconcile_readback_hash(
 TEXT,TEXT,TEXT,JSONB,TEXT,TEXT,BIGINT);

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
   WHERE request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_continuation_claim_requests
   WHERE request_id=NEW.request_id) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_RECONCILE_REQUEST_CONFLICT'
   USING ERRCODE='55000';
 END IF;
 RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION _agent_runtime_scheduled_wecom_continuation_request_guard() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
 PERFORM _agent_runtime_scheduled_wecom_global_request_lock(NEW.request_id);
 IF NOT EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_delivery_items item
   WHERE(item.id,item.intent_id)=(NEW.item_id,NEW.intent_id)) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_CONTINUATION_IDENTITY_INVALID'
   USING ERRCODE='22023';
 END IF;
 IF EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_deliveries d
   WHERE d.claim_request_id=NEW.request_id
    AND(d.intent_id,d.claim_worker_id,d.lease_token,d.lease_expires_at,d.state_version)
     IS DISTINCT FROM(NEW.intent_id,NEW.worker_id,NEW.lease_token,NEW.lease_expires_at,
      NEW.delivery_state_version))
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_dispatch_attempts
   WHERE claim_request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_prepared_recovery_requests
   WHERE request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_outcome_requests
   WHERE request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_reconcile_claim_requests
   WHERE request_id=NEW.request_id) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_CONTINUATION_REQUEST_CONFLICT'
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
   WHERE request_id=guard_request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_continuation_claim_requests
   WHERE request_id=guard_request_id) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_RECONCILE_REQUEST_CONFLICT'
   USING ERRCODE='55000';
 END IF;
 RETURN NEW;
END $$;

RESET ROLE;
