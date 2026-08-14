-- Roll back 228.06 only after the projection lane has drained completely.
SET LOCAL ROLE everydayai_owner;

DO $$
BEGIN
    IF to_regclass('public.agent_runtime_media_retry_lineage') IS NOT NULL THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_CONTROLS_MUST_ROLL_BACK_FIRST'
            USING ERRCODE = '55000';
    END IF;
    IF EXISTS (
           SELECT 1 FROM agent_actions action
            WHERE action.status NOT IN ('completed','failed','rejected','cancelled')
              AND (
                  EXISTS (SELECT 1 FROM agent_runtime_media_action_bindings binding
                          WHERE binding.action_id=action.id)
                  OR EXISTS (SELECT 1 FROM agent_runtime_prepared_media_action_bindings binding
                          WHERE binding.action_id=action.id)
              )
       )
       OR EXISTS (
           SELECT 1 FROM agent_projection_outbox outbox
           JOIN agent_runtime_events event ON event.id=outbox.event_id
            WHERE outbox.status<>'delivered'
              AND (
                  EXISTS (SELECT 1 FROM agent_runtime_media_action_bindings binding
                          WHERE binding.action_id IS NOT DISTINCT FROM event.action_id
                             OR binding.run_id IS NOT DISTINCT FROM event.run_id)
                  OR EXISTS (SELECT 1 FROM agent_runtime_prepared_media_action_bindings binding
                          WHERE binding.action_id IS NOT DISTINCT FROM event.action_id
                             OR binding.run_id IS NOT DISTINCT FROM event.run_id)
              )
       )
       OR EXISTS (SELECT 1 FROM agent_runtime_media_action_bindings
                   WHERE credit_state='pending')
       OR EXISTS (SELECT 1 FROM agent_runtime_prepared_media_action_bindings
                   WHERE credit_state='pending') THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_PROJECTION_IN_USE'
            USING ERRCODE = '55000';
    END IF;
END $$;

DROP FUNCTION read_agent_runtime_media_projection_result_v1(UUID);
DROP FUNCTION requeue_agent_runtime_media_projection_v1(UUID,BIGINT,INTEGER,UUID,TEXT,TIMESTAMPTZ);
DROP FUNCTION fail_agent_runtime_media_projection_v1(UUID,UUID,TEXT);
DROP FUNCTION apply_agent_runtime_media_projection_v1(UUID,UUID,TEXT,JSONB);
DROP FUNCTION _agent_runtime_media_run_projection_v1(agent_runtime_events,TEXT);
DROP FUNCTION _agent_runtime_media_action_only_run_v1(UUID);
DROP FUNCTION _agent_runtime_media_merge_run_content_v1(UUID,JSONB);
DROP FUNCTION _agent_runtime_media_prepared_action_projection_v1(agent_runtime_events,JSONB);
DROP FUNCTION read_agent_runtime_media_projection_v1(UUID,UUID);
DROP FUNCTION claim_agent_runtime_media_projection_v1(INTEGER,INTEGER);
DROP FUNCTION _agent_runtime_media_slot_update_v1(UUID,UUID,INTEGER,TEXT,BIGINT,JSONB);
DROP FUNCTION _agent_runtime_media_action_projection_v1(agent_runtime_events,JSONB);
DROP FUNCTION register_agent_runtime_media_asset_v1(UUID,JSONB);
DROP FUNCTION _agent_runtime_media_action_facts_v1(agent_runtime_events);
DROP FUNCTION _agent_runtime_media_projection_action_v1(agent_runtime_events);
DROP FUNCTION _agent_runtime_media_projection_scope_v1();
DROP TABLE agent_runtime_media_projection_results;
DROP TABLE agent_runtime_media_projection_checkpoints;
DROP TABLE agent_runtime_media_projection_recoveries;
DROP INDEX idx_agent_runtime_media_bindings_slot;
DROP TRIGGER agent_runtime_media_binding_slot_default_v1
    ON agent_runtime_media_action_bindings;
DROP FUNCTION _agent_runtime_media_binding_slot_default_v1();
ALTER TABLE agent_runtime_media_action_bindings DROP COLUMN slot_id;
ALTER TABLE agent_runtime_prepared_media_action_bindings
    DROP COLUMN credit_state,
    DROP COLUMN projection_revision,
    DROP COLUMN state_version;

-- 228.06 temporarily fences the legacy claim lane; restore its exact 220.12
-- definitions when the additive projection lane is rolled back.
CREATE OR REPLACE FUNCTION _agent_compat_projection_action(
    p_event agent_runtime_events
) RETURNS TEXT LANGUAGE plpgsql IMMUTABLE
SET search_path = pg_catalog, public AS $$
BEGIN
    IF p_event.event_version <> 1 THEN
        RAISE EXCEPTION 'AGENT_COMPAT_EVENT_VERSION_UNSUPPORTED'
            USING ERRCODE = '22023';
    END IF;
    RETURN CASE
        WHEN p_event.event_type = 'command.accepted' THEN 'user_message'
        WHEN p_event.event_type = 'run.created' THEN 'run_pending'
        WHEN p_event.event_type IN ('run.claimed', 'run.resumed') THEN 'run_running'
        WHEN p_event.event_type = 'run.waiting' THEN 'run_waiting'
        WHEN p_event.event_type = 'run.completed' THEN 'run_completed'
        WHEN p_event.event_type = 'run.failed' THEN 'run_failed'
        WHEN p_event.event_type = 'run.cancelled' THEN 'run_cancelled'
        WHEN p_event.event_type IN (
            'action.requested', 'action.accepted', 'action.retry_scheduled',
            'action.unknown', 'action.completed', 'action.failed',
            'action.cancelled'
        ) THEN 'action_progress'
        WHEN p_event.event_type IN (
            'session.created', 'command.attempts_exhausted',
            'model_step.created', 'model_step.completed', 'model_step.failed'
        ) THEN 'checkpoint_only'
        ELSE NULL
    END;
END;
$$;

CREATE OR REPLACE FUNCTION claim_agent_compat_projection_outbox(
    p_batch_size INTEGER DEFAULT 50, p_lease_seconds INTEGER DEFAULT 60
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_rows JSONB;
BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);
    IF p_batch_size NOT BETWEEN 1 AND 100
       OR p_lease_seconds NOT BETWEEN 15 AND 300 THEN
        RAISE EXCEPTION 'AGENT_COMPAT_PROJECTION_CLAIM_INVALID'
            USING ERRCODE = '22023';
    END IF;
    INSERT INTO agent_compat_projection_checkpoints(
        session_id, projection_kind
    )
    SELECT DISTINCT outbox.session_id, outbox.projection_kind
      FROM agent_projection_outbox outbox
     WHERE outbox.projection_kind IN ('web_runtime', 'wecom')
    ON CONFLICT DO NOTHING;
    WITH eligible AS (
        SELECT outbox.id
          FROM agent_projection_outbox outbox
          JOIN agent_runtime_events event ON event.id = outbox.event_id
          JOIN agent_compat_projection_checkpoints checkpoint
            ON checkpoint.session_id = outbox.session_id
           AND checkpoint.projection_kind = outbox.projection_kind
         WHERE outbox.projection_kind IN ('web_runtime', 'wecom')
           AND outbox.next_attempt_at <= clock_timestamp()
           AND (
               outbox.status = 'pending'
               OR (outbox.status = 'processing'
                   AND outbox.lease_expires_at <= clock_timestamp())
           )
           AND event.sequence > checkpoint.through_sequence
           AND NOT EXISTS (
               SELECT 1
                 FROM agent_projection_outbox earlier
                 JOIN agent_runtime_events earlier_event
                   ON earlier_event.id = earlier.event_id
                WHERE earlier.session_id = outbox.session_id
                  AND earlier.projection_kind = outbox.projection_kind
                  AND earlier_event.sequence < event.sequence
                  AND earlier_event.sequence > checkpoint.through_sequence
                  AND earlier.status <> 'delivered'
           )
         ORDER BY outbox.next_attempt_at, event.occurred_at, outbox.id
         FOR UPDATE OF outbox SKIP LOCKED
         LIMIT p_batch_size
    ), claimed AS (
        UPDATE agent_projection_outbox outbox SET status = 'processing',
               attempt_count = attempt_count + 1,
               lease_token = gen_random_uuid(),
               lease_expires_at = clock_timestamp()
                   + make_interval(secs => p_lease_seconds),
               updated_at = clock_timestamp()
          FROM eligible WHERE outbox.id = eligible.id
        RETURNING outbox.*
    )
    SELECT COALESCE(jsonb_agg(to_jsonb(claimed)), '[]'::JSONB)
      INTO v_rows FROM claimed;
    RETURN v_rows;
END;
$$;

RESET ROLE;
