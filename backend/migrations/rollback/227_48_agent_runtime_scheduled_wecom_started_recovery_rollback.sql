-- Roll back 227_48 only when no durable started-recovery facts remain.

SET LOCAL ROLE everydayai_owner;

LOCK TABLE agent_runtime_scheduled_wecom_started_recovery_requests IN SHARE ROW EXCLUSIVE MODE;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_started_recovery_requests)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_outcome_requests outcome_request
  JOIN agent_runtime_scheduled_wecom_started_recovery_requests recovery
   ON recovery.outcome_request_id=outcome_request.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_dispatch_attempts attempt
  JOIN agent_runtime_scheduled_wecom_started_recovery_requests recovery ON recovery.attempt_id=attempt.id
  WHERE attempt.status='unknown' AND attempt.dispatch_phase='ambiguous') THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_STARTED_RECOVERY_ROLLBACK_HAS_FACTS' USING ERRCODE='55000';
 END IF;
END $$;

REVOKE ALL ON FUNCTION recover_agent_runtime_scheduled_wecom_started_dispatch_v1(UUID,TEXT)
 FROM everydayai_wecom_runtime;
DROP FUNCTION recover_agent_runtime_scheduled_wecom_started_dispatch_v1(UUID,TEXT);
DROP FUNCTION _agent_runtime_scheduled_wecom_started_recovery_json(
 agent_runtime_scheduled_wecom_started_recovery_requests,TEXT);

CREATE OR REPLACE FUNCTION _agent_runtime_scheduled_wecom_unsupported_request_guard() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
 PERFORM _agent_runtime_scheduled_wecom_global_request_lock(NEW.request_id);
 IF EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_deliveries
   WHERE claim_request_id=NEW.request_id OR reconcile_request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_dispatch_attempts WHERE claim_request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_prepared_recovery_requests WHERE request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_outcome_requests WHERE request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_reconcile_claim_requests WHERE request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_continuation_claim_requests WHERE request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_reconcile_result_requests WHERE request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_reconcile_definitive_requests WHERE request_id=NEW.request_id) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_UNSUPPORTED_REQUEST_CONFLICT' USING ERRCODE='55000';
 END IF;
 RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION _agent_runtime_scheduled_wecom_reconcile_request_guard() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
 PERFORM _agent_runtime_scheduled_wecom_global_request_lock(NEW.request_id);
 IF EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_deliveries WHERE claim_request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_dispatch_attempts WHERE claim_request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_prepared_recovery_requests WHERE request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_outcome_requests WHERE request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_continuation_claim_requests WHERE request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_reconcile_result_requests WHERE request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_reconcile_definitive_requests WHERE request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_unsupported_requests WHERE request_id=NEW.request_id) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_RECONCILE_REQUEST_CONFLICT' USING ERRCODE='55000';
 END IF;
 RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION _agent_runtime_scheduled_wecom_continuation_request_guard() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
 PERFORM _agent_runtime_scheduled_wecom_global_request_lock(NEW.request_id);
 IF NOT EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_delivery_items item
   WHERE(item.id,item.intent_id)=(NEW.item_id,NEW.intent_id)) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_CONTINUATION_IDENTITY_INVALID' USING ERRCODE='22023';
 END IF;
 IF EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_deliveries d
   WHERE d.claim_request_id=NEW.request_id
    AND(d.intent_id,d.claim_worker_id,d.lease_token,d.lease_expires_at,d.state_version)
     IS DISTINCT FROM(NEW.intent_id,NEW.worker_id,NEW.lease_token,NEW.lease_expires_at,
      NEW.delivery_state_version))
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_dispatch_attempts WHERE claim_request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_prepared_recovery_requests WHERE request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_outcome_requests WHERE request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_reconcile_claim_requests WHERE request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_reconcile_result_requests WHERE request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_reconcile_definitive_requests WHERE request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_unsupported_requests WHERE request_id=NEW.request_id) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_CONTINUATION_REQUEST_CONFLICT' USING ERRCODE='55000';
 END IF;
 RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION _agent_runtime_scheduled_wecom_reconcile_result_request_guard() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
 PERFORM _agent_runtime_scheduled_wecom_global_request_lock(NEW.request_id);
 IF EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_deliveries
   WHERE claim_request_id=NEW.request_id OR reconcile_request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_dispatch_attempts WHERE claim_request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_prepared_recovery_requests WHERE request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_outcome_requests WHERE request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_reconcile_claim_requests WHERE request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_continuation_claim_requests WHERE request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_reconcile_definitive_requests WHERE request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_unsupported_requests WHERE request_id=NEW.request_id) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_RECONCILE_RESULT_REQUEST_CONFLICT' USING ERRCODE='55000';
 END IF;
 RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION _agent_runtime_scheduled_wecom_reconcile_definitive_request_guard() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
 PERFORM _agent_runtime_scheduled_wecom_global_request_lock(NEW.request_id);
 IF EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_deliveries
   WHERE claim_request_id=NEW.request_id OR reconcile_request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_dispatch_attempts WHERE claim_request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_prepared_recovery_requests WHERE request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_outcome_requests WHERE request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_reconcile_claim_requests WHERE request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_continuation_claim_requests WHERE request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_reconcile_result_requests WHERE request_id=NEW.request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_unsupported_requests WHERE request_id=NEW.request_id) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_RECONCILE_DEFINITIVE_REQUEST_CONFLICT' USING ERRCODE='55000';
 END IF;
 RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION _agent_runtime_scheduled_wecom_legacy_request_guard() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE guard_request_id UUID;
BEGIN
 IF TG_TABLE_NAME='agent_runtime_scheduled_wecom_deliveries' THEN
  IF NEW.claim_request_id IS NULL OR NEW.claim_request_id IS NOT DISTINCT FROM OLD.claim_request_id THEN RETURN NEW; END IF;
  guard_request_id:=NEW.claim_request_id;
 ELSE guard_request_id:=NEW.request_id;
 END IF;
 PERFORM _agent_runtime_scheduled_wecom_global_request_lock(guard_request_id);
 IF EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_reconcile_claim_requests WHERE request_id=guard_request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_continuation_claim_requests WHERE request_id=guard_request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_reconcile_result_requests WHERE request_id=guard_request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_reconcile_definitive_requests WHERE request_id=guard_request_id)
 OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_wecom_unsupported_requests WHERE request_id=guard_request_id) THEN
  RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_WECOM_RECONCILE_REQUEST_CONFLICT' USING ERRCODE='55000';
 END IF;
 RETURN NEW;
END $$;

DROP TRIGGER runtime_scheduled_wecom_started_recovery_global_request_guard
 ON agent_runtime_scheduled_wecom_started_recovery_requests;
DROP FUNCTION _agent_runtime_scheduled_wecom_started_recovery_request_guard();
DROP TRIGGER runtime_scheduled_wecom_started_recovery_immutable
 ON agent_runtime_scheduled_wecom_started_recovery_requests;
DROP FUNCTION _agent_runtime_scheduled_wecom_started_recovery_immutable();
DROP TABLE agent_runtime_scheduled_wecom_started_recovery_requests;

RESET ROLE;
