-- 277: Freeze legacy image sources inside the existing Actor claim transaction.
-- No message backfill, billing change, new table, or widening revision predicates.
RESET ROLE;
SET LOCAL ROLE everydayai_owner;

CREATE OR REPLACE FUNCTION public.freeze_chat_image_sources(
    p_task public.tasks, p_conversation public.conversations
) RETURNS JSONB LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE v_catalog JSONB; v_messages JSONB := '[]'::JSONB; v_row RECORD;
    v_content JSONB; v_base BIGINT; v_through UUID; v_unavailable TEXT;
BEGIN
    IF p_task.conversation_id IS DISTINCT FROM p_conversation.id
       OR (p_conversation.scope_type='user' AND p_task.user_id IS DISTINCT FROM p_conversation.user_id)
       OR p_task.org_id IS DISTINCT FROM p_conversation.org_id THEN
        RAISE EXCEPTION 'IMAGE_SOURCE_CATALOG_SCOPE_DENIED' USING ERRCODE='42501';
    END IF;
    -- First claim overwrites any client-supplied private JSON. Reclaims never
    -- discover additional legacy images, including late terminal transitions.
    IF p_task.execution_attempt > 0 AND p_task.request_params ? '_image_sources_v1' THEN
        v_catalog := p_task.request_params->'_image_sources_v1';
        IF v_catalog->>'version' IS DISTINCT FROM '1'
           OR v_catalog->>'task_id' IS DISTINCT FROM p_task.id::TEXT
           OR v_catalog->>'conversation_id' IS DISTINCT FROM p_task.conversation_id::TEXT
           OR v_catalog->>'input_message_id' IS DISTINCT FROM p_task.input_message_id::TEXT
           OR v_catalog->>'user_id' IS DISTINCT FROM p_task.user_id::TEXT
           OR v_catalog->>'org_id' IS DISTINCT FROM p_task.org_id::TEXT
           OR v_catalog->>'base_revision' IS DISTINCT FROM p_task.base_context_revision::TEXT
           OR v_catalog->>'through_message_id' IS DISTINCT FROM p_task.context_through_message_id::TEXT
           OR jsonb_typeof(v_catalog->'messages') IS DISTINCT FROM 'array' THEN
            RAISE EXCEPTION 'IMAGE_SOURCE_CATALOG_INVALID' USING ERRCODE='22023';
        END IF;
        RETURN v_catalog;
    END IF;
    v_base := CASE WHEN p_task.execution_attempt > 0 THEN p_task.base_context_revision
                   ELSE p_conversation.context_revision END;
    v_through := CASE WHEN p_task.execution_attempt > 0 THEN p_task.context_through_message_id
                      ELSE p_conversation.last_closed_message_id END;
    -- Old attempts/checkpoints lacking this catalog retain their old strict
    -- boundary: don't retroactively discover sources during recovery.
    IF p_task.execution_attempt = 0 AND p_conversation.scope_type = 'user' THEN
        FOR v_row IN SELECT id, conversation_id, org_id, role, status, message_kind, content, created_at
            FROM public.messages m
            WHERE m.conversation_id=p_task.conversation_id AND m.org_id IS NOT DISTINCT FROM p_task.org_id
              AND m.context_revision IS NULL AND m.message_kind='conversation'
              AND m.role IN ('user','assistant') AND m.status IN ('completed','interrupted')
              AND m.id<>p_task.input_message_id
              AND (m.turn_id IS NULL OR EXISTS (
                  SELECT 1 FROM public.tasks t WHERE t.conversation_id=m.conversation_id
                    AND t.org_id IS NOT DISTINCT FROM m.org_id AND t.turn_id=m.turn_id
                    AND (t.delivery_context @> '{"actor":true}'::JSONB) IS DISTINCT FROM TRUE
                    AND t.status IN ('completed','failed','cancelled')))
              AND NOT EXISTS (
                  SELECT 1 FROM public.tasks t WHERE t.conversation_id=m.conversation_id
                    AND t.org_id IS NOT DISTINCT FROM m.org_id AND t.status IN ('pending','running')
                    AND (t.input_message_id=m.id OR t.assistant_message_id=m.id OR t.turn_id=m.turn_id))
            ORDER BY m.created_at,m.id
        LOOP
            BEGIN v_content := v_row.content::JSONB;
            EXCEPTION WHEN invalid_text_representation THEN CONTINUE; END;
            IF jsonb_typeof(v_content)<>'array' THEN CONTINUE; END IF;
            IF EXISTS(SELECT 1 FROM jsonb_array_elements(v_content) p
                      WHERE p->>'type'='image' AND COALESCE(p->>'failed','false')='false') THEN
                v_messages := v_messages || jsonb_build_array(jsonb_build_object(
                    'id',v_row.id,'conversation_id',v_row.conversation_id,'org_id',v_row.org_id,
                    'role',v_row.role,'status',v_row.status,'message_kind',v_row.message_kind,
                    'context_revision',NULL,'content',v_content,'created_at',v_row.created_at));
                IF octet_length(v_messages::TEXT)>120000 OR jsonb_array_length(v_messages)>100 THEN
                    v_messages := '[]'::JSONB;
                    v_unavailable := 'IMAGE_SOURCE_CATALOG_TOO_LARGE_SELECT_EXPLICIT_IMAGES';
                    EXIT;
                END IF;
            END IF;
        END LOOP;
    END IF;
    RETURN jsonb_build_object('version',1,'task_id',p_task.id,'conversation_id',p_task.conversation_id,
        'input_message_id',p_task.input_message_id,'org_id',p_task.org_id,'user_id',p_task.user_id,
        'base_revision',v_base,'through_message_id',v_through,'messages',v_messages,'unavailable_reason',v_unavailable);
END;
$$;
REVOKE ALL ON FUNCTION public.freeze_chat_image_sources(public.tasks,public.conversations) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.freeze_chat_image_sources(public.tasks,public.conversations)
    TO everydayai, everydayai_worker, everydayai_owner;

-- Protect the private catalog from arbitrary JSON updates. First-claim input
-- is re-derived from actual scoped rows; subsequent attempts cannot replace it.
CREATE OR REPLACE FUNCTION public.guard_chat_image_sources()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE v_conversation public.conversations%ROWTYPE; v_expected JSONB;
BEGIN
    IF OLD.type<>'chat' OR (OLD.delivery_context @> '{"actor":true}'::JSONB) IS DISTINCT FROM TRUE THEN RETURN NEW; END IF;
    IF OLD.execution_attempt>0 AND OLD.request_params ? '_image_sources_v1' THEN
        IF NEW.request_params->'_image_sources_v1' IS DISTINCT FROM OLD.request_params->'_image_sources_v1' THEN
            RAISE EXCEPTION 'IMAGE_SOURCE_CATALOG_IMMUTABLE' USING ERRCODE='22023';
        END IF;
    ELSIF NEW.request_params ? '_image_sources_v1' THEN
        IF NEW.status<>'running' OR NEW.execution_attempt<>OLD.execution_attempt+1 OR NEW.execution_token IS NULL THEN
            RAISE EXCEPTION 'IMAGE_SOURCE_CATALOG_PRIVATE' USING ERRCODE='42501';
        END IF;
        SELECT * INTO v_conversation FROM public.conversations WHERE id=OLD.conversation_id;
        v_expected := public.freeze_chat_image_sources(OLD,v_conversation);
        IF NEW.request_params->'_image_sources_v1' IS DISTINCT FROM v_expected THEN
            RAISE EXCEPTION 'IMAGE_SOURCE_CATALOG_INVALID' USING ERRCODE='22023';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION public.guard_chat_image_sources() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.guard_chat_image_sources() TO everydayai, everydayai_worker;
RESET ROLE;
SET LOCAL ROLE everydayai;
DROP TRIGGER IF EXISTS guard_chat_image_sources ON public.tasks;
CREATE TRIGGER guard_chat_image_sources BEFORE UPDATE OF request_params ON public.tasks
    FOR EACH ROW EXECUTE FUNCTION public.guard_chat_image_sources();
RESET ROLE;

-- Preserve the deployed owner and ACL of each shared claim RPC. The release
-- session may SET ROLE to the two controlled migration/application owners.
DO $owner$ DECLARE v_owner TEXT; BEGIN
    SELECT pg_get_userbyid(proowner) INTO v_owner FROM pg_proc
        WHERE oid='public.claim_next_serial_generation_turn(uuid,integer,integer)'::regprocedure;
    IF v_owner NOT IN ('everydayai','everydayai_owner') THEN
        RAISE EXCEPTION 'CHAT_IMAGE_CLAIM_OWNER_UNSUPPORTED: %',v_owner;
    END IF;
    EXECUTE format('SET LOCAL ROLE %I',v_owner);
END $owner$;
CREATE OR REPLACE FUNCTION claim_next_serial_generation_turn(
    p_conversation_id UUID,
    p_lease_seconds INTEGER DEFAULT 90,
    p_max_attempts INTEGER DEFAULT 3
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_conversation conversations%ROWTYPE;
    v_owner tasks%ROWTYPE;
    v_task tasks%ROWTYPE;
    v_token UUID;
    v_sources JSONB;
BEGIN
    IF p_lease_seconds NOT BETWEEN 15 AND 300 OR p_max_attempts < 1 THEN
        RAISE EXCEPTION 'ACTOR_CLAIM_ARGUMENT_INVALID' USING ERRCODE = '22023';
    END IF;

    SELECT * INTO v_conversation
      FROM conversations
     WHERE id = p_conversation_id
     FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'ACTOR_CONVERSATION_NOT_FOUND' USING ERRCODE = 'P0002';
    END IF;

    IF v_conversation.active_serial_task_id IS NOT NULL THEN
        SELECT * INTO v_owner
          FROM tasks
         WHERE id = v_conversation.active_serial_task_id
         FOR UPDATE;
        IF v_owner.id IS NOT NULL
           AND v_owner.status = 'running'
           AND v_owner.lease_expires_at > NOW() THEN
            RETURN jsonb_build_object('outcome', 'busy');
        END IF;
        IF v_owner.id IS NOT NULL AND v_owner.status = 'running' THEN
            IF v_owner.execution_attempt >= p_max_attempts THEN
                UPDATE tasks
                   SET status = 'failed',
                       terminal_reason = 'lease_attempts_exhausted',
                       completed_at = NOW(),
                       execution_token = NULL,
                       lease_expires_at = NULL
                 WHERE id = v_owner.id;
            ELSE
                UPDATE tasks
                   SET status = 'pending',
                       terminal_reason = 'lease_expired',
                       execution_token = NULL,
                       lease_expires_at = NULL
                 WHERE id = v_owner.id;
            END IF;
        END IF;
        UPDATE conversations
           SET active_serial_task_id = NULL,
               actor_updated_at = NOW()
         WHERE id = p_conversation_id;
    END IF;

    SELECT * INTO v_task
      FROM tasks
     WHERE conversation_id = p_conversation_id
       AND type = 'chat'
       AND delivery_context @> '{"actor": true}'::JSONB
       AND execution_mode = 'serial'
       AND status = 'pending'
       AND input_message_id IS NOT NULL
       AND turn_id IS NOT NULL
     ORDER BY queue_sequence, id
     FOR UPDATE SKIP LOCKED
     LIMIT 1;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('outcome', 'empty');
    END IF;

    v_sources := public.freeze_chat_image_sources(v_task, v_conversation);
    v_token := uuid_generate_v4();
    UPDATE tasks
       SET status = 'running',
           execution_token = v_token,
           lease_expires_at = NOW() + make_interval(secs => p_lease_seconds),
           execution_attempt = execution_attempt + 1,
           started_at = COALESCE(started_at, NOW()),
           request_params = jsonb_set(COALESCE(request_params,'{}'::JSONB), '{_image_sources_v1}', v_sources),
           base_context_revision = (v_sources->>'base_revision')::BIGINT,
           context_through_message_id = (v_sources->>'through_message_id')::UUID,
           terminal_reason = NULL
     WHERE id = v_task.id
     RETURNING * INTO v_task;

    UPDATE conversations
       SET active_serial_task_id = v_task.id,
           actor_updated_at = NOW()
     WHERE id = p_conversation_id;

    RETURN jsonb_build_object(
        'outcome', 'claimed',
        'task_id', v_task.id,
        'execution_token', v_token,
        'turn_id', v_task.turn_id,
        'input_message_id', v_task.input_message_id,
        'base_context_revision', v_task.base_context_revision,
        'context_through_message_id', v_task.context_through_message_id,
        'execution_attempt', v_task.execution_attempt
    );
END;
$$;

RESET ROLE;
DO $owner$ DECLARE v_owner TEXT; BEGIN
    SELECT pg_get_userbyid(proowner) INTO v_owner FROM pg_proc
        WHERE oid='public.claim_branch_generation_turn(uuid,integer,integer)'::regprocedure;
    IF v_owner NOT IN ('everydayai','everydayai_owner') THEN
        RAISE EXCEPTION 'CHAT_IMAGE_CLAIM_OWNER_UNSUPPORTED: %',v_owner;
    END IF;
    EXECUTE format('SET LOCAL ROLE %I',v_owner);
END $owner$;
CREATE OR REPLACE FUNCTION claim_branch_generation_turn(
    p_task_id UUID,
    p_lease_seconds INTEGER DEFAULT 90,
    p_max_attempts INTEGER DEFAULT 3
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_conversation conversations%ROWTYPE;
    v_task tasks%ROWTYPE;
    v_token UUID;
    v_sources JSONB;
    v_conversation_id UUID;
BEGIN
    IF p_lease_seconds NOT BETWEEN 15 AND 300 OR p_max_attempts < 1 THEN
        RAISE EXCEPTION 'ACTOR_CLAIM_ARGUMENT_INVALID' USING ERRCODE = '22023';
    END IF;
    SELECT conversation_id INTO v_conversation_id FROM tasks WHERE id = p_task_id;
    IF v_conversation_id IS NULL THEN
        RAISE EXCEPTION 'ACTOR_TASK_NOT_FOUND' USING ERRCODE = 'P0002';
    END IF;
    SELECT * INTO v_conversation
      FROM conversations
     WHERE id = v_conversation_id
     FOR UPDATE;
    SELECT * INTO v_task FROM tasks WHERE id = p_task_id FOR UPDATE;
    IF v_task.id IS NULL
       OR v_task.type <> 'chat'
       OR NOT (v_task.delivery_context @> '{"actor": true}'::JSONB)
       OR v_task.execution_mode <> 'branch'
       OR v_task.input_message_id IS NULL
       OR v_task.turn_id IS NULL THEN
        RAISE EXCEPTION 'ACTOR_BRANCH_NOT_CLAIMABLE' USING ERRCODE = '55000';
    END IF;
    IF v_task.status = 'running' AND v_task.lease_expires_at > NOW() THEN
        RETURN jsonb_build_object('outcome', 'busy');
    END IF;
    IF v_task.status = 'running' AND v_task.execution_attempt >= p_max_attempts THEN
        UPDATE tasks
           SET status = 'failed',
               terminal_reason = 'lease_attempts_exhausted',
               completed_at = NOW(),
               execution_token = NULL,
               lease_expires_at = NULL
         WHERE id = p_task_id;
        RETURN jsonb_build_object('outcome', 'attempts_exhausted');
    END IF;
    IF v_task.status NOT IN ('pending', 'running') THEN
        RETURN jsonb_build_object('outcome', 'terminal', 'status', v_task.status);
    END IF;

    v_sources := public.freeze_chat_image_sources(v_task, v_conversation);
    v_token := uuid_generate_v4();
    UPDATE tasks
       SET status = 'running',
           execution_token = v_token,
           lease_expires_at = NOW() + make_interval(secs => p_lease_seconds),
           execution_attempt = execution_attempt + 1,
           started_at = COALESCE(started_at, NOW()),
           request_params = jsonb_set(COALESCE(request_params,'{}'::JSONB), '{_image_sources_v1}', v_sources),
           base_context_revision = (v_sources->>'base_revision')::BIGINT,
           context_through_message_id = (v_sources->>'through_message_id')::UUID,
           terminal_reason = NULL
     WHERE id = p_task_id
     RETURNING * INTO v_task;

    RETURN jsonb_build_object(
        'outcome', 'claimed',
        'task_id', v_task.id,
        'execution_token', v_token,
        'turn_id', v_task.turn_id,
        'input_message_id', v_task.input_message_id,
        'base_context_revision', v_task.base_context_revision,
        'context_through_message_id', v_task.context_through_message_id,
        'execution_attempt', v_task.execution_attempt
    );
END;
$$;

RESET ROLE;
