/* 228.06: Runtime media Action -> Task/slot/credit projection owner. */ /* This lane is additive.  It deliberately owns no Provider submission. */
SET LOCAL ROLE everydayai_owner;
CREATE TABLE agent_runtime_media_projection_checkpoints (     session_id UUID NOT NULL REFERENCES agent_runtime_sessions(id)
        ON DELETE RESTRICT,     projection_kind TEXT NOT NULL
        CHECK (projection_kind IN ('web_runtime', 'wecom')),     through_sequence BIGINT NOT NULL DEFAULT 0 CHECK (through_sequence >= 0),
    last_event_id UUID REFERENCES agent_runtime_events(id) ON DELETE RESTRICT,     state_version BIGINT NOT NULL DEFAULT 0 CHECK (state_version >= 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),     PRIMARY KEY (session_id, projection_kind)
);
CREATE TABLE agent_runtime_media_projection_results (     outbox_id UUID PRIMARY KEY
        REFERENCES agent_projection_outbox(id) ON DELETE RESTRICT,     event_id UUID NOT NULL REFERENCES agent_runtime_events(id)
        ON DELETE RESTRICT,     session_id UUID NOT NULL REFERENCES agent_runtime_sessions(id)
        ON DELETE RESTRICT,     org_id UUID REFERENCES organizations(id) ON DELETE RESTRICT,
    user_id UUID REFERENCES users(id) ON DELETE RESTRICT,     projection_kind TEXT NOT NULL
        CHECK (projection_kind IN ('web_runtime', 'wecom')),     event_sequence BIGINT NOT NULL CHECK (event_sequence > 0),
    projection_action TEXT NOT NULL CHECK (projection_action IN (         'checkpoint_only', 'action_progress', 'run_pending', 'run_running',
        'run_waiting', 'run_completed', 'run_failed', 'run_cancelled'     )),
    action_id UUID REFERENCES agent_actions(id) ON DELETE RESTRICT,     message_id UUID REFERENCES messages(id) ON DELETE RESTRICT,
    task_id UUID REFERENCES tasks(id) ON DELETE RESTRICT,     slot_id UUID,
    slot_index INTEGER CHECK (slot_index IS NULL OR slot_index BETWEEN 0 AND 9),     slot_status TEXT CHECK (slot_status IS NULL OR slot_status IN (
        'pending', 'accepted', 'unknown', 'completed', 'failed', 'cancelled'     )),
    slot_revision BIGINT CHECK (slot_revision IS NULL OR slot_revision >= 0),     content_part JSONB,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),     UNIQUE (session_id, projection_kind, event_sequence)
);
/* A retry creates a new Action but must continue projecting into the original */ /* message slot.  Initial actions use their Action id as the stable slot id; */
/* later additive control migrations can bind retries to that same value. */ ALTER TABLE agent_runtime_media_action_bindings ADD COLUMN slot_id UUID;
UPDATE agent_runtime_media_action_bindings SET slot_id = action_id;
CREATE FUNCTION _agent_runtime_media_binding_slot_default_v1()
RETURNS TRIGGER LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
BEGIN
    NEW.slot_id := COALESCE(NEW.slot_id, NEW.action_id);
    RETURN NEW;
END;
$$;
CREATE TRIGGER agent_runtime_media_binding_slot_default_v1
BEFORE INSERT ON agent_runtime_media_action_bindings
FOR EACH ROW EXECUTE FUNCTION _agent_runtime_media_binding_slot_default_v1();
ALTER TABLE agent_runtime_media_action_bindings ALTER COLUMN slot_id SET NOT NULL;
CREATE INDEX idx_agent_runtime_media_bindings_slot     ON agent_runtime_media_action_bindings(output_message_id, slot_id, created_at DESC);
 ALTER TABLE agent_runtime_media_projection_checkpoints ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_media_projection_results ENABLE ROW LEVEL SECURITY; CREATE POLICY agent_runtime_media_projection_checkpoints_owner_all
    ON agent_runtime_media_projection_checkpoints     FOR ALL TO everydayai_owner USING (TRUE) WITH CHECK (TRUE);
CREATE POLICY agent_runtime_media_projection_results_owner_all     ON agent_runtime_media_projection_results
    FOR ALL TO everydayai_owner USING (TRUE) WITH CHECK (TRUE); ALTER TABLE agent_runtime_media_projection_checkpoints FORCE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_media_projection_results FORCE ROW LEVEL SECURITY;
CREATE FUNCTION _agent_runtime_media_projection_scope_v1() RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$ BEGIN
    IF session_user <> 'everydayai_projection_worker'        OR current_setting('app.access_kind', TRUE) IS DISTINCT FROM 'projection' THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_PROJECTION_SCOPE_REQUIRED'             USING ERRCODE = '42501';
    END IF; END;
$$;
CREATE FUNCTION _agent_runtime_media_projection_action_v1(     p_event agent_runtime_events
) RETURNS TEXT LANGUAGE plpgsql IMMUTABLE SET search_path = pg_catalog, public AS $$
BEGIN     IF p_event.event_version <> 1 THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_PROJECTION_EVENT_VERSION_UNSUPPORTED'             USING ERRCODE = '22023';
    END IF;     RETURN CASE
        WHEN p_event.event_type IN (             'action.requested', 'action.accepted', 'action.unknown',
            'action.completed', 'action.failed', 'action.rejected',             'action.cancelled'
        ) THEN 'action_progress'         WHEN p_event.event_type = 'run.created' THEN 'run_pending'
        WHEN p_event.event_type IN ('run.claimed', 'run.resumed')             THEN 'run_running'
        WHEN p_event.event_type = 'run.waiting' THEN 'run_waiting'         WHEN p_event.event_type = 'run.completed' THEN 'run_completed'
        WHEN p_event.event_type = 'run.failed' THEN 'run_failed'         WHEN p_event.event_type = 'run.cancelled' THEN 'run_cancelled'
        ELSE 'checkpoint_only'     END;
END; $$;
 /* The legacy lane must leave Runtime-media events to this owner.  The */
/* projection worker still keeps the old lane for non-media Runtime events. */ CREATE OR REPLACE FUNCTION _agent_compat_projection_action(
    p_event agent_runtime_events ) RETURNS TEXT LANGUAGE plpgsql IMMUTABLE
SET search_path = pg_catalog, public AS $$ BEGIN
    IF p_event.event_version <> 1 THEN         RAISE EXCEPTION 'AGENT_COMPAT_EVENT_VERSION_UNSUPPORTED'
            USING ERRCODE = '22023';     END IF;
    RETURN CASE         WHEN p_event.event_type = 'command.accepted' THEN 'user_message'
        WHEN p_event.event_type = 'run.created' THEN 'run_pending'         WHEN p_event.event_type IN ('run.claimed', 'run.resumed') THEN 'run_running'
        WHEN p_event.event_type = 'run.waiting' THEN 'run_waiting'         WHEN p_event.event_type = 'run.completed' THEN 'run_completed'
        WHEN p_event.event_type = 'run.failed' THEN 'run_failed'         WHEN p_event.event_type = 'run.cancelled' THEN 'run_cancelled'
        WHEN p_event.event_type IN (             'action.requested', 'action.accepted', 'action.retry_scheduled',
            'action.unknown', 'action.completed', 'action.failed',             'action.rejected', 'action.cancelled'
        ) THEN 'action_progress'         WHEN p_event.event_type IN (
            'session.created', 'command.attempts_exhausted',             'model_step.created', 'model_step.completed', 'model_step.failed'
        ) THEN 'checkpoint_only'         ELSE NULL
    END; END;
$$;
CREATE OR REPLACE FUNCTION claim_agent_compat_projection_outbox(     p_batch_size INTEGER DEFAULT 50, p_lease_seconds INTEGER DEFAULT 60
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE v_rows JSONB; BEGIN
    PERFORM _assert_agent_runtime_actor(TRUE);     IF p_batch_size NOT BETWEEN 1 AND 100
       OR p_lease_seconds NOT BETWEEN 15 AND 300 THEN         RAISE EXCEPTION 'AGENT_COMPAT_PROJECTION_CLAIM_INVALID'
            USING ERRCODE = '22023';     END IF;
    INSERT INTO agent_compat_projection_checkpoints(session_id, projection_kind)     SELECT DISTINCT outbox.session_id, outbox.projection_kind
      FROM agent_projection_outbox outbox      WHERE outbox.projection_kind IN ('web_runtime', 'wecom')
    ON CONFLICT DO NOTHING;     WITH eligible AS (
        SELECT outbox.id           FROM agent_projection_outbox outbox
          JOIN agent_runtime_events event ON event.id = outbox.event_id           JOIN agent_compat_projection_checkpoints checkpoint
            ON checkpoint.session_id = outbox.session_id            AND checkpoint.projection_kind = outbox.projection_kind
         WHERE outbox.projection_kind IN ('web_runtime', 'wecom')            AND outbox.next_attempt_at <= clock_timestamp()
           AND (outbox.status = 'pending' OR (outbox.status = 'processing'                 AND outbox.lease_expires_at <= clock_timestamp()))
           AND event.sequence > checkpoint.through_sequence            AND NOT EXISTS (
               SELECT 1 FROM agent_runtime_media_action_bindings binding                 WHERE binding.action_id IS NOT DISTINCT FROM event.action_id
                   OR binding.run_id IS NOT DISTINCT FROM event.run_id            )
           AND NOT EXISTS (                SELECT 1 FROM agent_projection_outbox earlier
               JOIN agent_runtime_events earlier_event                  ON earlier_event.id = earlier.event_id
                WHERE earlier.session_id = outbox.session_id                   AND earlier.projection_kind = outbox.projection_kind
                  AND earlier_event.sequence < event.sequence                   AND earlier_event.sequence > checkpoint.through_sequence
                  AND earlier.status <> 'delivered'            )
         ORDER BY outbox.next_attempt_at, event.occurred_at, outbox.id          FOR UPDATE OF outbox SKIP LOCKED
         LIMIT p_batch_size     ), claimed AS (
        UPDATE agent_projection_outbox outbox SET status = 'processing',                attempt_count = attempt_count + 1, lease_token = gen_random_uuid(),
               lease_expires_at = clock_timestamp()                    + make_interval(secs => p_lease_seconds),
               updated_at = clock_timestamp()           FROM eligible WHERE outbox.id = eligible.id
        RETURNING outbox.*     )
    SELECT COALESCE(jsonb_agg(to_jsonb(claimed)), '[]'::JSONB)       INTO v_rows FROM claimed;
    RETURN v_rows; END;
$$;
CREATE FUNCTION _agent_runtime_media_action_facts_v1(     p_event agent_runtime_events
) RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE     v_action agent_actions%ROWTYPE;
    v_binding agent_runtime_media_action_bindings%ROWTYPE;     v_task tasks%ROWTYPE;
    v_message messages%ROWTYPE;     v_result agent_action_results%ROWTYPE;
    v_provider agent_runtime_provider_submission_facts%ROWTYPE;     v_urls JSONB := '[]'::JSONB;
    v_data JSONB; BEGIN
    SELECT * INTO v_action FROM agent_actions WHERE id = p_event.action_id;     IF v_action.id IS NULL THEN
        RETURN jsonb_build_object('outcome', 'not_found');     END IF;
    SELECT * INTO v_binding FROM agent_runtime_media_action_bindings      WHERE action_id = p_event.action_id;
    IF v_binding.action_id IS NULL THEN         RETURN jsonb_build_object('outcome', 'not_prepared',
            'action', to_jsonb(v_action));     END IF;
    SELECT * INTO v_task FROM tasks WHERE id = v_binding.task_id;     SELECT * INTO v_message FROM messages WHERE id = v_binding.output_message_id;
    IF v_action.session_id IS DISTINCT FROM p_event.session_id        OR v_action.run_id IS DISTINCT FROM p_event.run_id
       OR v_action.model_step_id IS DISTINCT FROM p_event.model_step_id        OR v_action.org_id IS DISTINCT FROM p_event.org_id
       OR v_action.user_id IS DISTINCT FROM p_event.user_id        OR v_binding.session_id IS DISTINCT FROM v_action.session_id
       OR v_binding.run_id IS DISTINCT FROM v_action.run_id        OR v_binding.model_step_id IS DISTINCT FROM v_action.model_step_id
       OR v_binding.org_id IS DISTINCT FROM v_action.org_id        OR v_binding.user_id IS DISTINCT FROM v_action.user_id
       OR v_task.id IS NULL OR v_task.user_id IS DISTINCT FROM v_binding.user_id        OR v_task.org_id IS DISTINCT FROM v_binding.org_id
       OR v_task.conversation_id IS DISTINCT FROM v_binding.conversation_id        OR v_task.assistant_message_id IS DISTINCT FROM v_binding.output_message_id
       OR v_message.id IS NULL        OR v_message.conversation_id IS DISTINCT FROM v_binding.conversation_id
       OR v_message.org_id IS DISTINCT FROM v_binding.org_id        OR v_message.role::TEXT <> 'assistant' THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_FACT_SCOPE_INVALID'             USING ERRCODE = '42501';
    END IF;     SELECT * INTO v_result FROM agent_action_results WHERE action_id = p_event.action_id;
    SELECT * INTO v_provider FROM agent_runtime_provider_submission_facts      WHERE action_id = p_event.action_id
     ORDER BY updated_at DESC, id DESC LIMIT 1;     IF v_result.action_id IS NOT NULL THEN
        v_data := COALESCE(v_result.data, '{}'::JSONB);         IF jsonb_typeof(v_data->'image_urls') = 'array' THEN
            SELECT COALESCE(jsonb_agg(value), '[]'::JSONB) INTO v_urls               FROM jsonb_array_elements(v_data->'image_urls') item(value)
             WHERE jsonb_typeof(value) = 'string'                AND NULLIF(BTRIM(value #>> '{}'), '') IS NOT NULL;
        ELSIF jsonb_typeof(v_data->'urls') = 'array' THEN             SELECT COALESCE(jsonb_agg(value), '[]'::JSONB) INTO v_urls
              FROM jsonb_array_elements(v_data->'urls') item(value)              WHERE jsonb_typeof(value) = 'string'
               AND NULLIF(BTRIM(value #>> '{}'), '') IS NOT NULL;         ELSIF jsonb_typeof(v_data->'images') = 'array' THEN
            SELECT COALESCE(jsonb_agg(value->>'url'), '[]'::JSONB) INTO v_urls               FROM jsonb_array_elements(v_data->'images') item(value)
             WHERE jsonb_typeof(value) = 'object'                AND NULLIF(BTRIM(value->>'url'), '') IS NOT NULL;
        END IF;     END IF;
    RETURN jsonb_build_object(         'outcome', 'found', 'action', to_jsonb(v_action),
        'binding', to_jsonb(v_binding), 'task', to_jsonb(v_task),         'result', CASE WHEN v_result.action_id IS NULL THEN NULL ELSE to_jsonb(v_result) END,
        'provider', CASE WHEN v_provider.id IS NULL THEN NULL ELSE to_jsonb(v_provider) END,         'result_urls', v_urls
    ); END;
$$;
CREATE FUNCTION _agent_runtime_media_slot_update_v1(     p_message_id UUID, p_slot_id UUID, p_slot_index INTEGER,
    p_slot_status TEXT, p_slot_revision BIGINT, p_content_part JSONB DEFAULT NULL ) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$ DECLARE
    v_message messages%ROWTYPE;     v_content JSONB;
    v_slot JSONB;     v_updated JSONB;
    v_ordinality INTEGER;     v_current_revision BIGINT;
BEGIN     SELECT * INTO v_message FROM messages WHERE id = p_message_id FOR UPDATE;
    IF v_message.id IS NULL OR v_message.role::TEXT <> 'assistant'        OR jsonb_typeof(v_message.content::JSONB) IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_OUTPUT_MESSAGE_INVALID'             USING ERRCODE = '55000';
    END IF;     v_content := v_message.content::JSONB;
    SELECT part, ordinality::INTEGER INTO v_slot, v_ordinality       FROM jsonb_array_elements(v_content) WITH ORDINALITY source(part, ordinality)
     WHERE part->>'slot_id' = p_slot_id::TEXT        AND (part->>'slot_index')::INTEGER = p_slot_index;
    IF v_slot IS NULL THEN         RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_SLOT_NOT_FOUND'
            USING ERRCODE = '55000';     END IF;
    v_current_revision := COALESCE((v_slot->>'slot_revision')::BIGINT, 0);     IF p_slot_revision < v_current_revision THEN
        RETURN v_slot;     END IF;
    v_updated := COALESCE(p_content_part, v_slot) || jsonb_build_object(         'type', 'image', 'slot_id', p_slot_id,
        'slot_index', p_slot_index, 'slot_status', p_slot_status,         'slot_revision', p_slot_revision
    );     v_content := jsonb_set(
        v_content, ARRAY[(v_ordinality - 1)::TEXT], v_updated, FALSE     );
    UPDATE messages SET content = v_content::TEXT WHERE id = p_message_id;     RETURN v_updated;
END; $$;
 CREATE FUNCTION register_agent_runtime_media_asset_v1(
    p_action_id UUID, p_payload JSONB ) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$ DECLARE
    v_binding agent_runtime_media_action_bindings%ROWTYPE;     v_task tasks%ROWTYPE;
    v_user users%ROWTYPE;     v_url TEXT;
    v_storage_provider TEXT;     v_storage_key TEXT;
    v_payload JSONB; BEGIN
    PERFORM _agent_runtime_media_projection_scope_v1();     IF jsonb_typeof(p_payload) IS DISTINCT FROM 'object' THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_ASSET_PAYLOAD_INVALID'             USING ERRCODE = '22023';
    END IF;     SELECT * INTO v_binding FROM agent_runtime_media_action_bindings
     WHERE action_id = p_action_id;     SELECT * INTO v_task FROM tasks WHERE id = v_binding.task_id;
    SELECT * INTO v_user FROM users WHERE id = v_binding.user_id;     v_url := NULLIF(BTRIM(p_payload->>'url'), '');
    v_storage_provider := NULLIF(BTRIM(p_payload->>'storage_provider'), '');     v_storage_key := NULLIF(BTRIM(p_payload->>'storage_key'), '');
    IF v_binding.action_id IS NULL OR v_task.id IS NULL OR v_user.id IS NULL        OR v_task.user_id IS DISTINCT FROM v_binding.user_id
       OR v_task.org_id IS DISTINCT FROM v_binding.org_id        OR v_task.assistant_message_id IS DISTINCT FROM v_binding.output_message_id
       OR v_task.delivery_context @> '{"runtime":true}'::JSONB IS NOT TRUE        OR v_url IS NULL OR v_storage_provider NOT IN ('workspace','oss')
       OR v_storage_key IS NULL OR NULLIF(BTRIM(p_payload->>'name'),'') IS NULL        OR NULLIF(BTRIM(p_payload->>'download_url'), '') IS NULL THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_ASSET_SCOPE_INVALID'             USING ERRCODE = '42501';
    END IF;     v_payload := jsonb_build_object(
        'type', 'image', 'url', v_url,         'download_url', p_payload->>'download_url',
        'original_url', COALESCE(NULLIF(BTRIM(p_payload->>'original_url'),''), v_url),         'workspace_path', NULLIF(BTRIM(p_payload->>'workspace_path'),''),
        'thumbnail_url', NULLIF(BTRIM(p_payload->>'thumbnail_url'),''),         'name', p_payload->>'name', 'mime_type', NULLIF(BTRIM(p_payload->>'mime_type'),''),
        'size', CASE WHEN p_payload->>'size' ~ '^[0-9]+$'                      THEN (p_payload->>'size')::BIGINT ELSE NULL END
    );     RETURN register_user_asset(
        v_binding.org_id, 'user', v_binding.user_id::TEXT,         v_storage_provider, v_storage_key, 'image',
        v_payload->>'original_url', v_payload->>'thumbnail_url',         v_payload->>'download_url', v_payload->>'workspace_path',
        v_payload->>'name', v_payload->>'mime_type',         CASE WHEN v_payload->>'size' IS NULL THEN NULL
             ELSE (v_payload->>'size')::BIGINT END,         NULLIF(BTRIM(p_payload->>'content_sha256'),''),
        jsonb_build_object('runtime_identity',             'runtime-media:' || p_action_id::TEXT || ':' || v_binding.slot_id::TEXT,
            'source_url', p_payload->>'source_url'),         'runtime-media:' || p_action_id::TEXT || ':' || v_binding.slot_id::TEXT,
        v_binding.user_id, 'generated', 'image_task', 'task',         v_binding.conversation_id, v_binding.output_message_id,
        v_binding.task_id, NULL, NULL, v_binding.action_index,         v_binding.pricing_model_id,
        NULLIF(BTRIM(p_payload->>'prompt'), ''), clock_timestamp()     );
END; $$;
 CREATE FUNCTION claim_agent_runtime_media_projection_v1(
    p_batch_size INTEGER DEFAULT 50, p_lease_seconds INTEGER DEFAULT 60 ) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$ DECLARE v_rows JSONB;
BEGIN     PERFORM _agent_runtime_media_projection_scope_v1();
    IF p_batch_size NOT BETWEEN 1 AND 100        OR p_lease_seconds NOT BETWEEN 15 AND 300 THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_PROJECTION_CLAIM_INVALID'             USING ERRCODE = '22023';
    END IF;     INSERT INTO agent_runtime_media_projection_checkpoints(session_id, projection_kind)
    SELECT DISTINCT outbox.session_id, outbox.projection_kind       FROM agent_projection_outbox outbox
     WHERE outbox.projection_kind IN ('web_runtime', 'wecom')     ON CONFLICT DO NOTHING;
    WITH eligible AS (         SELECT outbox.id
          FROM agent_projection_outbox outbox           JOIN agent_runtime_events event ON event.id = outbox.event_id
          JOIN agent_runtime_media_projection_checkpoints checkpoint             ON checkpoint.session_id = outbox.session_id
           AND checkpoint.projection_kind = outbox.projection_kind          WHERE outbox.projection_kind IN ('web_runtime', 'wecom')
           AND outbox.next_attempt_at <= clock_timestamp()            AND (outbox.status = 'pending' OR (outbox.status = 'processing'
                AND outbox.lease_expires_at <= clock_timestamp()))            AND event.sequence > checkpoint.through_sequence
           AND event.session_id = outbox.session_id            AND event.org_id IS NOT DISTINCT FROM outbox.org_id
           AND event.user_id IS NOT DISTINCT FROM outbox.user_id            AND EXISTS (
               SELECT 1 FROM agent_runtime_media_action_bindings binding                 WHERE binding.action_id IS NOT DISTINCT FROM event.action_id
                   OR binding.run_id IS NOT DISTINCT FROM event.run_id            )
           AND NOT EXISTS (                SELECT 1
                 FROM agent_projection_outbox earlier                  JOIN agent_runtime_events earlier_event
                   ON earlier_event.id = earlier.event_id                 WHERE earlier.session_id = outbox.session_id
                  AND earlier.projection_kind = outbox.projection_kind                   AND earlier_event.sequence < event.sequence
                  AND earlier_event.sequence > checkpoint.through_sequence                   AND earlier.status <> 'delivered'
           )          ORDER BY outbox.next_attempt_at, event.occurred_at, outbox.id
         FOR UPDATE OF outbox SKIP LOCKED          LIMIT p_batch_size
    ), claimed AS (         UPDATE agent_projection_outbox outbox SET status = 'processing',
               attempt_count = attempt_count + 1,                lease_token = gen_random_uuid(),
               lease_expires_at = clock_timestamp()                    + make_interval(secs => p_lease_seconds),
               updated_at = clock_timestamp()           FROM eligible WHERE outbox.id = eligible.id
        RETURNING outbox.*     )
    SELECT COALESCE(jsonb_agg(to_jsonb(claimed)), '[]'::JSONB)       INTO v_rows FROM claimed;
    RETURN v_rows; END;
$$;
CREATE FUNCTION read_agent_runtime_media_projection_v1(     p_outbox_id UUID, p_lease_token UUID
) RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE     v_outbox agent_projection_outbox%ROWTYPE;
    v_event agent_runtime_events%ROWTYPE;     v_result agent_runtime_media_projection_results%ROWTYPE;
    v_facts JSONB; BEGIN
    PERFORM _agent_runtime_media_projection_scope_v1();     SELECT * INTO v_outbox FROM agent_projection_outbox WHERE id = p_outbox_id;
    IF v_outbox.id IS NULL THEN RETURN jsonb_build_object('outcome','not_found'); END IF;     SELECT * INTO v_result FROM agent_runtime_media_projection_results
     WHERE outbox_id = p_outbox_id;     IF v_result.outbox_id IS NOT NULL THEN
        RETURN jsonb_build_object('outcome','already_applied','result',to_jsonb(v_result));     END IF;
    IF v_outbox.status <> 'processing'        OR v_outbox.lease_token IS DISTINCT FROM p_lease_token
       OR v_outbox.lease_expires_at <= clock_timestamp() THEN         RETURN jsonb_build_object('outcome','ownership_lost');
    END IF;     SELECT * INTO v_event FROM agent_runtime_events WHERE id = v_outbox.event_id;
    IF v_event.id IS NULL OR v_event.session_id IS DISTINCT FROM v_outbox.session_id        OR v_event.org_id IS DISTINCT FROM v_outbox.org_id
       OR v_event.user_id IS DISTINCT FROM v_outbox.user_id THEN         RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_PROJECTION_EVENT_SCOPE_INVALID'
            USING ERRCODE = '55000';     END IF;
    IF v_event.action_id IS NOT NULL THEN         v_facts := _agent_runtime_media_action_facts_v1(v_event);
    END IF;     RETURN jsonb_build_object(
        'outcome','found','outbox',to_jsonb(v_outbox),'event',to_jsonb(v_event),         'action_facts',v_facts
    ); END;
$$;
CREATE FUNCTION _agent_runtime_media_merge_run_content_v1(     p_message_id UUID, p_final JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE     v_content JSONB;
    v_slots JSONB := '[]'::JSONB;     v_other JSONB := '[]'::JSONB;
BEGIN     SELECT content::JSONB INTO v_content FROM messages
     WHERE id = p_message_id FOR UPDATE;     IF jsonb_typeof(v_content) IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_OUTPUT_MESSAGE_INVALID'             USING ERRCODE = '55000';
    END IF;     SELECT COALESCE(jsonb_agg(current_part ORDER BY binding.action_index), '[]'::JSONB)
      INTO v_slots       FROM (
          SELECT DISTINCT ON (candidate.action_index)                  candidate.action_index, candidate.slot_id
            FROM agent_runtime_media_action_bindings candidate            WHERE candidate.output_message_id = p_message_id
           ORDER BY candidate.action_index, candidate.created_at DESC,                     candidate.action_id DESC
      ) binding       JOIN LATERAL (
          SELECT part AS current_part FROM jsonb_array_elements(v_content) part            WHERE part->>'slot_id' = binding.slot_id::TEXT LIMIT 1
      ) current_slot ON TRUE;     SELECT COALESCE(jsonb_agg(part ORDER BY ordinality)
               FILTER (WHERE part->>'slot_id' IS NULL), '[]'::JSONB)       INTO v_other FROM jsonb_array_elements(v_content)
           WITH ORDINALITY source(part, ordinality);     RETURN v_slots || v_other || jsonb_build_array(p_final);
END; $$;
 CREATE FUNCTION _agent_runtime_media_run_projection_v1(
    p_event agent_runtime_events, p_action TEXT,     OUT projected_message_id UUID, OUT projected_task_id UUID,
    OUT content_part JSONB ) RETURNS RECORD LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$ DECLARE
    v_run agent_runs%ROWTYPE;     v_session agent_runtime_sessions%ROWTYPE;
    v_command agent_session_commands%ROWTYPE;     v_task tasks%ROWTYPE;
    v_message messages%ROWTYPE;     v_step agent_model_steps%ROWTYPE;
    v_model_result agent_model_results%ROWTYPE;     v_final JSONB;
    v_content JSONB;     v_output_id UUID;
    v_task_id UUID; BEGIN
    SELECT * INTO v_run FROM agent_runs WHERE id = p_event.run_id FOR UPDATE;     SELECT * INTO v_session FROM agent_runtime_sessions WHERE id = p_event.session_id;
    SELECT * INTO v_command FROM agent_session_commands WHERE id = v_run.command_id;     IF v_run.id IS NULL OR v_run.session_id IS DISTINCT FROM p_event.session_id
       OR v_session.id IS NULL OR v_command.session_id IS DISTINCT FROM v_session.id        OR v_run.org_id IS DISTINCT FROM p_event.org_id
       OR v_run.user_id IS DISTINCT FROM p_event.user_id THEN         RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_RUN_SCOPE_INVALID'
            USING ERRCODE = '55000';     END IF;
    v_task_id := NULLIF(v_command.payload->>'task_id','')::UUID;     v_output_id := NULLIF(v_command.payload->>'output_message_id','')::UUID;
    SELECT * INTO v_task FROM tasks WHERE id = v_task_id FOR UPDATE;     SELECT * INTO v_message FROM messages WHERE id = v_output_id FOR UPDATE;
    IF v_task.id IS NULL OR v_message.id IS NULL        OR v_task.user_id IS DISTINCT FROM v_run.user_id
       OR v_task.org_id IS DISTINCT FROM v_run.org_id        OR v_task.conversation_id IS DISTINCT FROM v_session.conversation_id
       OR v_message.conversation_id IS DISTINCT FROM v_session.conversation_id        OR v_message.org_id IS DISTINCT FROM v_run.org_id
       OR v_message.role::TEXT <> 'assistant' THEN         RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_RUN_ANCHOR_INVALID'
            USING ERRCODE = '55000';     END IF;
    IF p_action = 'run_completed' THEN         SELECT * INTO v_step FROM agent_model_steps
         WHERE run_id = v_run.id ORDER BY step_number DESC LIMIT 1;         SELECT * INTO v_model_result FROM agent_model_results
         WHERE model_step_id = v_step.id;         IF v_run.status <> 'completed' OR v_step.id IS NULL
           OR v_step.status <> 'completed'            OR v_step.stop_reason NOT IN ('final','structured_final')
           OR v_model_result.id IS NULL            OR v_model_result.content_hash IS DISTINCT FROM v_run.result_hash
           OR v_model_result.content_hash IS DISTINCT FROM encode(digest(                convert_to(CASE WHEN v_model_result.output_kind = 'text'
                   THEN v_model_result.text_content                    ELSE v_model_result.structured_content::TEXT END, 'UTF8'),
               'sha256'), 'hex') THEN             RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_MODEL_RESULT_INVALID'
                USING ERRCODE = '55000';         END IF;
        v_final := CASE WHEN v_model_result.output_kind = 'text'             THEN jsonb_build_object('type','text','text',v_model_result.text_content)
            ELSE jsonb_build_object('type','data','data',v_model_result.structured_content) END;         v_content := _agent_runtime_media_merge_run_content_v1(v_message.id, v_final);
        UPDATE messages SET content = v_content::TEXT, status = 'completed'          WHERE id = v_message.id;
        UPDATE tasks SET status = 'completed', credits_locked = 0,                assistant_message_id = v_message.id,
               credits_used = COALESCE((SELECT sum(child.credits_used)                   FROM tasks child
                  JOIN agent_runtime_media_action_bindings b ON b.task_id = child.id                  WHERE b.output_message_id = v_message.id), 0),
               result = jsonb_build_object('runtime_run_id',v_run.id,                    'model_result_id',v_model_result.id,
                   'content_hash',v_model_result.content_hash),                completed_at = COALESCE(v_run.completed_at, clock_timestamp())
         WHERE id = v_task.id;     ELSE
        UPDATE tasks SET status = CASE p_action                    WHEN 'run_pending' THEN 'pending'
                   WHEN 'run_running' THEN 'running'                    WHEN 'run_waiting' THEN 'running'
                   WHEN 'run_failed' THEN 'failed'                    WHEN 'run_cancelled' THEN 'cancelled'
                   ELSE status END,                credits_locked = CASE WHEN p_action IN ('run_failed','run_cancelled')
                   THEN 0 ELSE credits_locked END,                error_message = CASE WHEN p_action = 'run_failed'
                   THEN v_run.terminal_reason ELSE error_message END,                completed_at = CASE WHEN p_action IN ('run_failed','run_cancelled')
                   THEN COALESCE(v_run.completed_at, clock_timestamp())                    ELSE completed_at END
         WHERE id = v_task.id;     END IF;
    projected_message_id := v_message.id;     projected_task_id := v_task.id;
    content_part := v_final; END;
$$;
CREATE FUNCTION _agent_runtime_media_action_projection_v1(     p_event agent_runtime_events, p_content_part JSONB DEFAULT NULL
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE     v_action agent_actions%ROWTYPE;
    v_binding agent_runtime_media_action_bindings%ROWTYPE;     v_task tasks%ROWTYPE;
    v_facts JSONB;     v_slot JSONB;
    v_status TEXT;     v_revision BIGINT := p_event.sequence;
    v_error TEXT;     v_provider_ref TEXT;
BEGIN     SELECT * INTO v_action FROM agent_actions WHERE id = p_event.action_id;
    SELECT * INTO v_binding FROM agent_runtime_media_action_bindings      WHERE action_id = p_event.action_id;
    IF v_action.id IS NULL THEN         RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_ACTION_NOT_FOUND' USING ERRCODE = '55000';
    END IF;     IF v_binding.action_id IS NULL THEN
        RETURN jsonb_build_object('projection_action','checkpoint_only');     END IF;
    IF v_binding.session_id IS DISTINCT FROM p_event.session_id        OR v_binding.run_id IS DISTINCT FROM p_event.run_id
       OR v_binding.model_step_id IS DISTINCT FROM p_event.model_step_id        OR v_binding.org_id IS DISTINCT FROM p_event.org_id
       OR v_binding.user_id IS DISTINCT FROM p_event.user_id THEN         RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_BINDING_SCOPE_INVALID'
            USING ERRCODE = '42501';     END IF;
    SELECT * INTO v_task FROM tasks WHERE id = v_binding.task_id FOR UPDATE;     IF v_task.id IS NULL OR v_task.user_id IS DISTINCT FROM v_binding.user_id
       OR v_task.org_id IS DISTINCT FROM v_binding.org_id        OR v_task.conversation_id IS DISTINCT FROM v_binding.conversation_id
       OR v_task.assistant_message_id IS DISTINCT FROM v_binding.output_message_id        OR v_task.credit_transaction_id IS DISTINCT FROM v_binding.credit_transaction_id
       OR v_task.delivery_context @> '{"runtime":true}'::JSONB IS NOT TRUE THEN         RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_TASK_SCOPE_INVALID' USING ERRCODE = '42501';
    END IF;     v_facts := _agent_runtime_media_action_facts_v1(p_event);
    v_provider_ref := NULLIF(BTRIM((v_facts->'provider')->>'provider_task_ref'), '');     IF p_event.event_type = 'action.accepted' AND v_provider_ref IS NULL THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_PROVIDER_FACT_REQUIRED' USING ERRCODE = '55000';     END IF;
    v_status := CASE p_event.event_type         WHEN 'action.requested' THEN 'pending'
        WHEN 'action.accepted' THEN 'accepted'         WHEN 'action.unknown' THEN 'unknown'
        WHEN 'action.completed' THEN 'completed'         WHEN 'action.failed' THEN 'failed'
        WHEN 'action.rejected' THEN 'failed'         WHEN 'action.cancelled' THEN 'cancelled'
    END;     IF p_event.event_type = 'action.completed' THEN
        IF (v_facts->'result'->>'action_id') IS NULL            OR jsonb_array_length(COALESCE(v_facts->'result_urls','[]'::JSONB)) = 0
           OR p_content_part IS NULL            OR jsonb_typeof(p_content_part) IS DISTINCT FROM 'object'
           OR NULLIF(BTRIM(p_content_part->>'url'),'') IS NULL            OR p_content_part->>'source_url' IS NULL
           OR NOT EXISTS (SELECT 1 FROM jsonb_array_elements_text(v_facts->'result_urls') u                            WHERE u = p_content_part->>'source_url') THEN
            RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_AUTHORITATIVE_RESULT_REQUIRED'                 USING ERRCODE = '55000';
        END IF;         UPDATE tasks SET status = 'completed', credits_locked = 0,
               credits_used = v_binding.unit_credits,                external_task_id = COALESCE(v_provider_ref, external_task_id),
               result = p_content_part, error_message = NULL,                completed_at = COALESCE(v_action.completed_at, clock_timestamp())
         WHERE id = v_task.id;         PERFORM settle_agent_runtime_media_credit_v1(p_event.action_id, p_event.sequence);
    ELSIF p_event.event_type IN ('action.failed','action.rejected','action.cancelled') THEN         v_error := COALESCE(v_facts->'result'->>'error_code', v_action.terminal_reason,
            CASE p_event.event_type WHEN 'action.cancelled' THEN 'action_cancelled'                 WHEN 'action.rejected' THEN 'action_rejected' ELSE 'action_failed' END);
        p_content_part := jsonb_build_object('type','image','url',NULL,'failed',TRUE,             'error_code',v_error,'error',v_error);
        UPDATE tasks SET status = CASE WHEN p_event.event_type = 'action.cancelled'                THEN 'cancelled' ELSE 'failed' END, credits_locked = 0, credits_used = 0,
               external_task_id = COALESCE(v_provider_ref, external_task_id),                result = p_content_part, error_message = v_error,
               completed_at = COALESCE(v_action.completed_at, clock_timestamp())          WHERE id = v_task.id;
        PERFORM refund_agent_runtime_media_credit_v1(p_event.action_id, p_event.sequence);     ELSE
        UPDATE tasks SET status = CASE WHEN p_event.event_type = 'action.requested'                THEN CASE WHEN status = 'preparing' THEN 'pending' ELSE status END
               ELSE 'running' END, credits_locked = v_binding.unit_credits,                credits_used = 0, external_task_id = COALESCE(v_provider_ref, external_task_id),
               started_at = COALESCE(started_at, clock_timestamp())          WHERE id = v_task.id;
    END IF;     v_slot := _agent_runtime_media_slot_update_v1(
        v_binding.output_message_id, v_binding.slot_id, v_binding.action_index,         v_status, v_revision, p_content_part);
    IF p_event.event_type NOT IN ('action.completed','action.failed',                                   'action.rejected','action.cancelled') THEN
        UPDATE agent_runtime_media_action_bindings SET projection_revision = p_event.sequence,                state_version = state_version + 1, updated_at = clock_timestamp()
         WHERE action_id = p_event.action_id;     END IF;
    RETURN jsonb_build_object('projection_action','action_progress','message_id',         v_binding.output_message_id,'task_id',v_binding.task_id,'slot',v_slot);
END; $$;
 CREATE FUNCTION apply_agent_runtime_media_projection_v1(
    p_outbox_id UUID, p_lease_token UUID, p_action TEXT,     p_content_part JSONB DEFAULT NULL
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE     v_outbox agent_projection_outbox%ROWTYPE; v_event agent_runtime_events%ROWTYPE;
    v_checkpoint agent_runtime_media_projection_checkpoints%ROWTYPE;     v_existing agent_runtime_media_projection_results%ROWTYPE; v_action_expected TEXT;
    v_slot JSONB;     v_message_id UUID; v_task_id UUID; v_content_part JSONB; v_action_projection JSONB;
    v_result agent_runtime_media_projection_results%ROWTYPE; BEGIN
    PERFORM _agent_runtime_media_projection_scope_v1();     SELECT * INTO v_outbox FROM agent_projection_outbox WHERE id = p_outbox_id FOR UPDATE;
    IF v_outbox.id IS NULL THEN RETURN jsonb_build_object('outcome','not_found'); END IF;     SELECT * INTO v_existing FROM agent_runtime_media_projection_results WHERE outbox_id = p_outbox_id;
    IF v_existing.outbox_id IS NOT NULL THEN         RETURN jsonb_build_object('outcome','already_applied','result',to_jsonb(v_existing));
    END IF;     IF v_outbox.status <> 'processing'
       OR v_outbox.lease_token IS DISTINCT FROM p_lease_token THEN         RETURN jsonb_build_object('outcome','ownership_lost');
    END IF;     IF v_outbox.lease_expires_at <= clock_timestamp() THEN
        RETURN jsonb_build_object('outcome','lease_expired');     END IF;
    SELECT * INTO v_event FROM agent_runtime_events WHERE id = v_outbox.event_id;     SELECT * INTO v_checkpoint FROM agent_runtime_media_projection_checkpoints
     WHERE session_id = v_outbox.session_id        AND projection_kind = v_outbox.projection_kind FOR UPDATE;
    IF v_event.id IS NULL OR v_checkpoint.session_id IS NULL        OR v_event.session_id IS DISTINCT FROM v_outbox.session_id
       OR v_event.org_id IS DISTINCT FROM v_outbox.org_id        OR v_event.user_id IS DISTINCT FROM v_outbox.user_id THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_PROJECTION_ASSOCIATION_INVALID' USING ERRCODE = '55000';     END IF;
    SELECT * INTO v_existing       FROM agent_runtime_media_projection_results
     WHERE session_id = v_event.session_id        AND projection_kind = v_outbox.projection_kind
       AND event_sequence = v_event.sequence;     IF v_existing.outbox_id IS NOT NULL THEN
        UPDATE agent_projection_outbox SET status = 'delivered',                checkpoint = jsonb_build_object('through_sequence', v_event.sequence,
                   'result_id', v_existing.outbox_id),                lease_token = NULL, lease_expires_at = NULL,
               delivered_at = COALESCE(delivered_at, clock_timestamp()),                updated_at = clock_timestamp()
         WHERE id = v_outbox.id AND lease_token = p_lease_token;         RETURN jsonb_build_object('outcome','already_applied',
            'result',to_jsonb(v_existing));     END IF;
    IF v_event.sequence <= v_checkpoint.through_sequence THEN         RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_PROJECTION_REORDERED' USING ERRCODE = '55000';
    END IF;     IF EXISTS (
        SELECT 1 FROM agent_projection_outbox earlier         JOIN agent_runtime_events earlier_event ON earlier_event.id = earlier.event_id
         WHERE earlier.session_id = v_outbox.session_id            AND earlier.projection_kind = v_outbox.projection_kind
           AND earlier_event.sequence < v_event.sequence            AND earlier_event.sequence > v_checkpoint.through_sequence
           AND earlier.status <> 'delivered'     ) THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_PROJECTION_GAP' USING ERRCODE = '55000';     END IF;
    v_action_expected := _agent_runtime_media_projection_action_v1(v_event);     IF p_action IS DISTINCT FROM v_action_expected THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_PROJECTION_ACTION_INVALID' USING ERRCODE = '22023';     END IF;
    IF v_action_expected = 'action_progress' AND v_event.action_id IS NOT NULL THEN         v_action_projection := _agent_runtime_media_action_projection_v1(v_event, p_content_part);
        v_action_expected := v_action_projection->>'projection_action';         v_message_id := (v_action_projection->>'message_id')::UUID;
        v_task_id := (v_action_projection->>'task_id')::UUID;         v_slot := v_action_projection->'slot';
    ELSIF v_action_expected LIKE 'run_%' THEN         SELECT projected_message_id, projected_task_id, content_part INTO v_message_id, v_task_id, v_content_part
          FROM _agent_runtime_media_run_projection_v1(v_event, v_action_expected) projected;     END IF;
    INSERT INTO agent_runtime_media_projection_results(         outbox_id, event_id, session_id, org_id, user_id, projection_kind,
        event_sequence, projection_action, action_id, message_id, task_id,         slot_id, slot_index, slot_status, slot_revision, content_part
    ) VALUES (         v_outbox.id, v_event.id, v_event.session_id, v_outbox.org_id,
        v_outbox.user_id, v_outbox.projection_kind, v_event.sequence,         v_action_expected, v_event.action_id, v_message_id, v_task_id,
        CASE WHEN v_slot IS NULL THEN NULL ELSE (v_slot->>'slot_id')::UUID END,         CASE WHEN v_slot IS NULL THEN NULL ELSE (v_slot->>'slot_index')::INTEGER END,
        CASE WHEN v_slot IS NULL THEN NULL ELSE v_slot->>'slot_status' END,         CASE WHEN v_slot IS NULL THEN NULL ELSE (v_slot->>'slot_revision')::BIGINT END,
        COALESCE(v_slot, v_content_part)     ) RETURNING * INTO v_result;
    UPDATE agent_runtime_media_projection_checkpoints SET         through_sequence = v_event.sequence, last_event_id = v_event.id,
        state_version = state_version + 1, updated_at = clock_timestamp()      WHERE session_id = v_event.session_id
       AND projection_kind = v_outbox.projection_kind;     UPDATE agent_projection_outbox SET status = 'delivered',
           checkpoint = jsonb_build_object('through_sequence',v_event.sequence,                'result_id',v_result.outbox_id), lease_token = NULL,
           lease_expires_at = NULL, delivered_at = clock_timestamp(),            updated_at = clock_timestamp()
     WHERE id = v_outbox.id;     RETURN jsonb_build_object(
        'outcome','applied','result',to_jsonb(v_result),         'notification',jsonb_build_object(
            'type','image_partial_update','message_id',v_result.message_id,             'task_id',v_result.task_id,'user_id',v_outbox.user_id,
            'org_id',v_outbox.org_id,             'slot_id',v_result.slot_id,'slot_index',v_result.slot_index,
            'slot_status',v_result.slot_status,'slot_revision',v_result.slot_revision,             'content_part',v_result.content_part
        )     );
END; $$;
 CREATE FUNCTION fail_agent_runtime_media_projection_v1(
    p_outbox_id UUID, p_lease_token UUID, p_error_code TEXT ) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$ DECLARE v_outbox agent_projection_outbox%ROWTYPE;
BEGIN     PERFORM _agent_runtime_media_projection_scope_v1();
    SELECT * INTO v_outbox FROM agent_projection_outbox WHERE id = p_outbox_id FOR UPDATE;     IF v_outbox.id IS NULL THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
    IF v_outbox.status <> 'processing'        OR v_outbox.lease_token IS DISTINCT FROM p_lease_token THEN
        RETURN jsonb_build_object('outcome','ownership_lost');     END IF;
    UPDATE agent_projection_outbox SET status = 'pending',            next_attempt_at = clock_timestamp() + interval '1 second',
           lease_token = NULL, lease_expires_at = NULL,            last_error_code = left(NULLIF(btrim(p_error_code), ''), 200),
           updated_at = clock_timestamp()      WHERE id = p_outbox_id;
    RETURN jsonb_build_object('outcome','failed'); END;
$$;
CREATE FUNCTION read_agent_runtime_media_projection_result_v1(     p_outbox_id UUID
) RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE v_result agent_runtime_media_projection_results%ROWTYPE; BEGIN
    PERFORM _agent_runtime_media_projection_scope_v1();     SELECT * INTO v_result FROM agent_runtime_media_projection_results
     WHERE outbox_id = p_outbox_id;     IF v_result.outbox_id IS NULL THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
    RETURN jsonb_build_object('outcome','found','result',to_jsonb(v_result)); END;
$$;
REVOKE ALL ON TABLE agent_runtime_media_projection_checkpoints,     agent_runtime_media_projection_results
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime, everydayai_worker,     everydayai_sync, everydayai, everydayai_agent_runtime_worker,
    everydayai_projection_worker, everydayai_authorization_worker,     everydayai_sandbox_worker, everydayai_runtime_admin;
REVOKE ALL ON FUNCTION     _agent_runtime_media_binding_slot_default_v1(),
    _agent_runtime_media_projection_scope_v1(),
    _agent_runtime_media_projection_action_v1(agent_runtime_events),     _agent_runtime_media_action_facts_v1(agent_runtime_events),
    _agent_runtime_media_slot_update_v1(UUID,UUID,INTEGER,TEXT,BIGINT,JSONB),     _agent_runtime_media_action_projection_v1(agent_runtime_events,JSONB),
    _agent_runtime_media_merge_run_content_v1(UUID,JSONB),     register_agent_runtime_media_asset_v1(UUID,JSONB),
    claim_agent_runtime_media_projection_v1(INTEGER,INTEGER),     read_agent_runtime_media_projection_v1(UUID,UUID),
    _agent_runtime_media_run_projection_v1(agent_runtime_events,TEXT),     apply_agent_runtime_media_projection_v1(UUID,UUID,TEXT,JSONB),
    fail_agent_runtime_media_projection_v1(UUID,UUID,TEXT),     read_agent_runtime_media_projection_result_v1(UUID)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime, everydayai_worker,     everydayai_sync, everydayai, everydayai_agent_runtime_worker,
    everydayai_projection_worker, everydayai_authorization_worker,     everydayai_sandbox_worker, everydayai_runtime_admin;
 GRANT EXECUTE ON FUNCTION
    claim_agent_runtime_media_projection_v1(INTEGER,INTEGER),     read_agent_runtime_media_projection_v1(UUID,UUID),
    register_agent_runtime_media_asset_v1(UUID,JSONB),     apply_agent_runtime_media_projection_v1(UUID,UUID,TEXT,JSONB),
    fail_agent_runtime_media_projection_v1(UUID,UUID,TEXT),     read_agent_runtime_media_projection_result_v1(UUID)
TO everydayai_projection_worker;
RESET ROLE;
