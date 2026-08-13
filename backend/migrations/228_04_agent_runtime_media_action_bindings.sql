-- 228.04: Atomically bind Runtime image Actions to legacy media Tasks and credits.
SET LOCAL ROLE everydayai_owner;

CREATE TABLE agent_runtime_media_pricing_facts (
    pricing_revision TEXT NOT NULL,
    model_id TEXT NOT NULL,
    resolution_key TEXT NOT NULL,
    user_credits INTEGER NOT NULL CHECK (user_credits > 0),
    active BOOLEAN NOT NULL,
    supports_resolution BOOLEAN NOT NULL,
    requires_image_input BOOLEAN NOT NULL,
    is_default_model BOOLEAN NOT NULL,
    fact_hash TEXT NOT NULL CHECK (fact_hash ~ '^[0-9a-f]{64}$'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (pricing_revision, model_id, resolution_key),
    CHECK (length(btrim(pricing_revision)) BETWEEN 1 AND 200),
    CHECK (length(btrim(model_id)) BETWEEN 1 AND 200),
    CHECK (resolution_key = 'default' OR resolution_key IN ('1K','2K','4K')),
    CHECK (supports_resolution = (resolution_key <> 'default'))
);

CREATE FUNCTION _agent_runtime_media_pricing_immutable_v1()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
BEGIN
    RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_PRICING_IMMUTABLE'
        USING ERRCODE = '55000';
END $$;

-- BEGIN GENERATED RUNTIME MEDIA PRICING FACTS
INSERT INTO agent_runtime_media_pricing_facts(
    pricing_revision, model_id, resolution_key, user_credits, active,
    supports_resolution, requires_image_input, is_default_model, fact_hash
) VALUES
    ('kie-image-pricing-v1', 'google/nano-banana', 'default', 5, TRUE, FALSE, FALSE, FALSE, 'bd1fce5491a9e555f361163c677400dca9e6f8d123e573ddd6b3ccd7e52c2281'),
    ('kie-image-pricing-v1', 'google/nano-banana-edit', 'default', 5, TRUE, FALSE, TRUE, FALSE, 'ef4f363f34324cf93bf2b5e0fa3d38c137f21717c2d09cb62b7fb0b6becf90fd'),
    ('kie-image-pricing-v1', 'gpt-image-2-image-to-image', '1K', 6, TRUE, TRUE, TRUE, FALSE, 'c720e660a6eb218cd7d78d09723c69a4a893cd81b411a2c44640519c7d2ab955'),
    ('kie-image-pricing-v1', 'gpt-image-2-image-to-image', '2K', 10, TRUE, TRUE, TRUE, FALSE, '1d28a8f4bbb260f107487d0688ae3d4a19887451e25f37d2c6ec236f4ccd4227'),
    ('kie-image-pricing-v1', 'gpt-image-2-image-to-image', '4K', 16, TRUE, TRUE, TRUE, FALSE, '18bfb08d102c6e8e5cdab5a276f657f40fee8028ca3aa59d921b2cdf0e5119b8'),
    ('kie-image-pricing-v1', 'gpt-image-2-text-to-image', '1K', 6, TRUE, TRUE, FALSE, TRUE, 'e316eaf8bb0c3d41660d015337b9e988d27d491d8820f2dc05b1fc692ad65c80'),
    ('kie-image-pricing-v1', 'gpt-image-2-text-to-image', '2K', 10, TRUE, TRUE, FALSE, TRUE, '19a6dbca91dfc628394c3de459964b8cd5228da145d99cf66523e8d49d598fe2'),
    ('kie-image-pricing-v1', 'gpt-image-2-text-to-image', '4K', 16, TRUE, TRUE, FALSE, TRUE, '2881e9114ad97087b9b133cb762a66d784d1f33e57031e9b88aa846676faac93'),
    ('kie-image-pricing-v1', 'nano-banana-pro', '1K', 25, TRUE, TRUE, FALSE, FALSE, 'e0d771585786c3a06394181187f300c828b8f4a6a8dc207e3d9c21da858429a6'),
    ('kie-image-pricing-v1', 'nano-banana-pro', '2K', 37, TRUE, TRUE, FALSE, FALSE, '83dcf84b7d202e12d888950076bf2371e3d8a235c21b239f0d5ffde6b4729b0d'),
    ('kie-image-pricing-v1', 'nano-banana-pro', '4K', 49, TRUE, TRUE, FALSE, FALSE, '05149fa4f6b7e670723d6eba1499fd12cb9f1c5c1d4e6a0e69cdf7da2ae71d2d');
-- END GENERATED RUNTIME MEDIA PRICING FACTS

CREATE TRIGGER agent_runtime_media_pricing_immutable
BEFORE INSERT OR UPDATE OR DELETE ON agent_runtime_media_pricing_facts
FOR EACH ROW EXECUTE FUNCTION _agent_runtime_media_pricing_immutable_v1();

CREATE UNIQUE INDEX uq_agent_runtime_media_default_pricing
    ON agent_runtime_media_pricing_facts(pricing_revision, resolution_key)
    WHERE is_default_model;

CREATE TABLE agent_runtime_media_action_bindings (
    action_id UUID PRIMARY KEY REFERENCES agent_actions(id) ON DELETE RESTRICT,
    task_id UUID NOT NULL UNIQUE REFERENCES tasks(id) ON DELETE RESTRICT,
    session_id UUID NOT NULL REFERENCES agent_runtime_sessions(id) ON DELETE RESTRICT,
    run_id UUID NOT NULL REFERENCES agent_runs(id) ON DELETE RESTRICT,
    model_step_id UUID NOT NULL REFERENCES agent_model_steps(id) ON DELETE RESTRICT,
    chat_task_id UUID NOT NULL REFERENCES tasks(id) ON DELETE RESTRICT,
    org_id UUID REFERENCES organizations(id) ON DELETE RESTRICT,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE RESTRICT,
    batch_hash TEXT NOT NULL CHECK (batch_hash ~ '^[0-9a-f]{64}$'),
    action_index INTEGER NOT NULL CHECK (action_index BETWEEN 0 AND 9),
    action_arguments_hash TEXT NOT NULL
        CHECK (action_arguments_hash ~ '^[0-9a-f]{64}$'),
    action_request_hash TEXT NOT NULL
        CHECK (action_request_hash ~ '^[0-9a-f]{64}$'),
    input_message_id UUID NOT NULL REFERENCES messages(id) ON DELETE RESTRICT,
    output_message_id UUID NOT NULL REFERENCES messages(id) ON DELETE RESTRICT,
    credit_transaction_id UUID NOT NULL UNIQUE
        REFERENCES credit_transactions(id) ON DELETE RESTRICT,
    pricing_revision TEXT NOT NULL,
    pricing_model_id TEXT NOT NULL,
    pricing_resolution TEXT NOT NULL,
    pricing_fact_hash TEXT NOT NULL CHECK (pricing_fact_hash ~ '^[0-9a-f]{64}$'),
    provider_request_hash TEXT NOT NULL
        CHECK (provider_request_hash ~ '^[0-9a-f]{64}$'),
    unit_credits INTEGER NOT NULL CHECK (unit_credits > 0),
    reference_manifest_hash TEXT NOT NULL
        CHECK (reference_manifest_hash ~ '^[0-9a-f]{64}$'),
    credit_state TEXT NOT NULL DEFAULT 'pending'
        CHECK (credit_state IN ('pending','confirmed','refunded')),
    projection_revision BIGINT NOT NULL DEFAULT 0 CHECK (projection_revision >= 0),
    state_version BIGINT NOT NULL DEFAULT 0 CHECK (state_version >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (model_step_id, action_index),
    FOREIGN KEY (pricing_revision, pricing_model_id, pricing_resolution)
        REFERENCES agent_runtime_media_pricing_facts(
            pricing_revision, model_id, resolution_key
        ) ON DELETE RESTRICT
);

CREATE INDEX idx_agent_runtime_media_bindings_batch
    ON agent_runtime_media_action_bindings(model_step_id, batch_hash, action_index);
CREATE INDEX idx_agent_runtime_media_bindings_output
    ON agent_runtime_media_action_bindings(output_message_id, action_index);

ALTER TABLE agent_runtime_media_pricing_facts ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_media_action_bindings ENABLE ROW LEVEL SECURITY;
CREATE POLICY agent_runtime_media_pricing_owner_all
    ON agent_runtime_media_pricing_facts FOR ALL TO everydayai_owner
    USING (TRUE) WITH CHECK (TRUE);
CREATE POLICY agent_runtime_media_bindings_owner_all
    ON agent_runtime_media_action_bindings FOR ALL TO everydayai_owner
    USING (TRUE) WITH CHECK (TRUE);
ALTER TABLE agent_runtime_media_pricing_facts FORCE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_media_action_bindings FORCE ROW LEVEL SECURITY;

CREATE FUNCTION _agent_runtime_media_worker_v1()
RETURNS VOID LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
BEGIN
    IF session_user <> 'everydayai_agent_runtime_worker'
       OR current_setting('app.access_kind', TRUE) IS DISTINCT FROM 'agent_runtime' THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_WORKER_SCOPE_REQUIRED'
            USING ERRCODE = '42501';
    END IF;
END $$;

CREATE FUNCTION _agent_runtime_media_projection_v1()
RETURNS VOID LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
BEGIN
    IF session_user <> 'everydayai_projection_worker'
       OR current_setting('app.access_kind', TRUE) IS DISTINCT FROM 'projection' THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_PROJECTION_SCOPE_REQUIRED'
            USING ERRCODE = '42501';
    END IF;
END $$;

CREATE FUNCTION _agent_runtime_media_input_manifest_v1(p_content TEXT)
RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    content JSONB;
    images JSONB;
BEGIN
    BEGIN
        content := p_content::JSONB;
    EXCEPTION WHEN invalid_text_representation THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_INPUT_CONTENT_INVALID'
            USING ERRCODE = '22023';
    END;
    IF jsonb_typeof(content) IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_INPUT_CONTENT_INVALID'
            USING ERRCODE = '22023';
    END IF;
    SELECT COALESCE(jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
        'index', image_index, 'asset_id', NULLIF(btrim(part->>'asset_id'), ''),
        'workspace_path', NULLIF(btrim(part->>'workspace_path'), ''),
        'url_hash', CASE WHEN NULLIF(btrim(COALESCE(
            part->>'url', part->>'original_url', part->>'download_url'
        )), '') IS NOT NULL THEN encode(digest(convert_to(btrim(COALESCE(
            part->>'url', part->>'original_url', part->>'download_url'
        )), 'UTF8'), 'sha256'), 'hex') END
    )) ORDER BY image_index), '[]'::JSONB) INTO images
    FROM (
        SELECT part, row_number() OVER (ORDER BY ordinality) - 1 AS image_index
        FROM jsonb_array_elements(content) WITH ORDINALITY source(part, ordinality)
        WHERE part->>'type' = 'image'
    ) image_parts;
    IF EXISTS (
        SELECT 1 FROM jsonb_array_elements(images) image
        WHERE COALESCE(image->>'asset_id', image->>'workspace_path', image->>'url_hash') IS NULL
    ) THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_INPUT_IMAGE_FACT_INVALID'
            USING ERRCODE = '22023';
    END IF;
    RETURN jsonb_build_object(
        'image_count', jsonb_array_length(images),
        'manifest_hash', encode(digest(convert_to(images::TEXT, 'UTF8'), 'sha256'), 'hex')
    );
END $$;

CREATE FUNCTION _agent_runtime_media_attempt_valid_v1(
    p_action_id UUID, p_attempt_id UUID, p_worker_id TEXT,
    p_execution_token UUID, p_expected_attempt_version BIGINT,
    p_request_hash TEXT
) RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public RETURN EXISTS (
    SELECT 1
    FROM agent_action_attempts attempt
    JOIN agent_actions action ON action.id = attempt.action_id
    JOIN agent_action_dispatch_intents intent ON intent.attempt_id = attempt.id
    WHERE action.id = p_action_id AND attempt.id = p_attempt_id
      AND attempt.worker_id = btrim(p_worker_id)
      AND attempt.execution_token = p_execution_token
      AND attempt.state_version = p_expected_attempt_version
      AND attempt.request_hash = p_request_hash
      AND action.request_hash = p_request_hash
      AND attempt.session_id = action.session_id
      AND attempt.run_id = action.run_id
      AND attempt.org_id IS NOT DISTINCT FROM action.org_id
      AND attempt.user_id = action.user_id
      AND (
          (attempt.status IN ('claimed','dispatching') AND action.status = 'running')
          OR (attempt.status = 'accepted' AND action.status = 'accepted')
          OR (attempt.status = 'unknown' AND action.status = 'unknown')
      )
      AND attempt.lease_expires_at > clock_timestamp()
      AND intent.action_id = action.id
      AND intent.execution_token = p_execution_token
      AND intent.request_hash = p_request_hash
      AND intent.executor_type = 'runtime_media_generation:generate_image'
      AND intent.executor_revision = 1
);

CREATE FUNCTION read_agent_runtime_media_manifest_v1(
    p_action_id UUID, p_attempt_id UUID, p_worker_id TEXT,
    p_execution_token UUID, p_expected_attempt_version BIGINT,
    p_request_hash TEXT
) RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    action agent_actions%ROWTYPE;
    run agent_runs%ROWTYPE;
    command agent_session_commands%ROWTYPE;
    input_message messages%ROWTYPE;
    manifest JSONB;
BEGIN
    PERFORM _agent_runtime_media_worker_v1();
    IF p_action_id IS NULL OR p_attempt_id IS NULL OR p_execution_token IS NULL
       OR p_expected_attempt_version < 0
       OR NULLIF(btrim(p_worker_id), '') IS NULL
       OR COALESCE(p_request_hash, '') !~ '^[0-9a-f]{64}$'
       OR NOT _agent_runtime_media_attempt_valid_v1(
           p_action_id, p_attempt_id, p_worker_id, p_execution_token,
           p_expected_attempt_version, p_request_hash
       ) THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_ATTEMPT_SCOPE_INVALID'
            USING ERRCODE = '42501';
    END IF;
    SELECT * INTO action FROM agent_actions WHERE id = p_action_id;
    SELECT * INTO run FROM agent_runs WHERE id = action.run_id;
    SELECT * INTO command FROM agent_session_commands WHERE id = run.command_id;
    SELECT * INTO input_message FROM messages
     WHERE id = NULLIF(command.payload->>'input_message_id', '')::UUID
       AND conversation_id = (
           SELECT conversation_id FROM agent_runtime_sessions WHERE id = action.session_id
       );
    IF action.tool_name <> 'generate_image' OR input_message.id IS NULL
       OR input_message.role::TEXT <> 'user' THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_INPUT_ANCHOR_INVALID'
            USING ERRCODE = '42501';
    END IF;
    manifest := _agent_runtime_media_input_manifest_v1(input_message.content);
    RETURN jsonb_build_object(
        'outcome', 'found',
        'reference_manifest_hash', manifest->>'manifest_hash',
        'input_image_count', (manifest->>'image_count')::INTEGER
    );
END $$;

CREATE FUNCTION prepare_agent_runtime_media_batch_v1(
    p_action_id UUID, p_attempt_id UUID, p_worker_id TEXT,
    p_execution_token UUID, p_expected_attempt_version BIGINT,
    p_request_hash TEXT, p_reference_manifest_hash TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    seed_action agent_actions%ROWTYPE;
    session agent_runtime_sessions%ROWTYPE;
    run agent_runs%ROWTYPE;
    step agent_model_steps%ROWTYPE;
    command agent_session_commands%ROWTYPE;
    chat_task tasks%ROWTYPE;
    input_message messages%ROWTYPE;
    output_message messages%ROWTYPE;
    app_user users%ROWTYPE;
    action agent_actions%ROWTYPE;
    pricing agent_runtime_media_pricing_facts%ROWTYPE;
    binding agent_runtime_media_action_bindings%ROWTYPE;
    manifest JSONB;
    refs JSONB;
    items JSONB := '[]'::JSONB;
    slots JSONB;
    item JSONB;
    action_count INTEGER;
    existing_count INTEGER;
    image_count INTEGER;
    total_credits INTEGER := 0;
    final_balance INTEGER;
    item_balance INTEGER;
    transaction_id UUID;
    selected_model_id TEXT;
    aspect_ratio TEXT;
    effective_resolution TEXT;
    resolution_key TEXT;
BEGIN
    PERFORM _agent_runtime_media_worker_v1();
    IF p_action_id IS NULL OR p_attempt_id IS NULL OR p_execution_token IS NULL
       OR p_expected_attempt_version < 0
       OR NULLIF(btrim(p_worker_id), '') IS NULL
       OR COALESCE(p_request_hash, '') !~ '^[0-9a-f]{64}$'
       OR COALESCE(p_reference_manifest_hash, '') !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_PREPARE_INVALID'
            USING ERRCODE = '22023';
    END IF;
    SELECT * INTO seed_action FROM agent_actions WHERE id = p_action_id;
    IF seed_action.id IS NULL THEN
        RETURN jsonb_build_object('outcome', 'not_found');
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended(
        'runtime-media-batch:' || seed_action.model_step_id::TEXT, 0
    ));
    SELECT * INTO session FROM agent_runtime_sessions
     WHERE id = seed_action.session_id FOR UPDATE;
    SELECT * INTO run FROM agent_runs WHERE id = seed_action.run_id FOR UPDATE;
    SELECT * INTO step FROM agent_model_steps
     WHERE id = seed_action.model_step_id FOR UPDATE;
    PERFORM id FROM agent_actions WHERE model_step_id = step.id
     ORDER BY action_index, id FOR UPDATE;
    SELECT * INTO seed_action FROM agent_actions WHERE id = p_action_id;
    SELECT * INTO command FROM agent_session_commands WHERE id = run.command_id FOR UPDATE;
    SELECT * INTO chat_task FROM tasks
     WHERE id = NULLIF(command.payload->>'task_id', '')::UUID FOR UPDATE;
    PERFORM id FROM messages WHERE id IN (
        NULLIF(command.payload->>'input_message_id', '')::UUID,
        NULLIF(command.payload->>'output_message_id', '')::UUID
    ) ORDER BY id FOR UPDATE;
    SELECT * INTO input_message FROM messages
     WHERE id = NULLIF(command.payload->>'input_message_id', '')::UUID;
    SELECT * INTO output_message FROM messages
     WHERE id = NULLIF(command.payload->>'output_message_id', '')::UUID;
    SELECT * INTO app_user FROM users WHERE id = session.user_id FOR UPDATE;

    SELECT count(*) INTO action_count FROM agent_actions WHERE model_step_id = step.id;
    IF session.id IS NULL OR run.id IS NULL OR step.id IS NULL OR command.id IS NULL
       OR chat_task.id IS NULL OR input_message.id IS NULL OR output_message.id IS NULL
       OR app_user.id IS NULL OR action_count NOT BETWEEN 1 AND 10
       OR seed_action.session_id IS DISTINCT FROM session.id
       OR seed_action.run_id IS DISTINCT FROM run.id
       OR seed_action.model_step_id IS DISTINCT FROM step.id
       OR run.session_id IS DISTINCT FROM session.id
       OR step.session_id IS DISTINCT FROM session.id OR step.run_id IS DISTINCT FROM run.id
       OR command.session_id IS DISTINCT FROM session.id
       OR run.command_id IS DISTINCT FROM command.id
       OR run.org_id IS DISTINCT FROM session.org_id OR step.org_id IS DISTINCT FROM session.org_id
       OR run.user_id IS DISTINCT FROM session.user_id OR step.user_id IS DISTINCT FROM session.user_id
       OR session.user_id IS NULL OR chat_task.user_id IS DISTINCT FROM session.user_id
       OR chat_task.org_id IS DISTINCT FROM session.org_id
       OR chat_task.conversation_id IS DISTINCT FROM session.conversation_id
       OR chat_task.type::TEXT <> 'chat'
       OR chat_task.input_message_id IS DISTINCT FROM input_message.id
       OR chat_task.assistant_message_id IS DISTINCT FROM output_message.id
       OR input_message.conversation_id IS DISTINCT FROM session.conversation_id
       OR output_message.conversation_id IS DISTINCT FROM session.conversation_id
       OR input_message.org_id IS DISTINCT FROM session.org_id
       OR output_message.org_id IS DISTINCT FROM session.org_id
       OR input_message.role::TEXT <> 'user' OR output_message.role::TEXT <> 'assistant'
       OR step.status <> 'completed' OR step.stop_reason <> 'tool_calls'
       OR EXISTS (
           SELECT 1 FROM agent_actions batch_action
           WHERE batch_action.model_step_id = step.id
             AND (batch_action.session_id IS DISTINCT FROM session.id
               OR batch_action.run_id IS DISTINCT FROM run.id
               OR batch_action.org_id IS DISTINCT FROM session.org_id
               OR batch_action.user_id IS DISTINCT FROM session.user_id
               OR batch_action.batch_hash IS DISTINCT FROM seed_action.batch_hash
               OR batch_action.tool_name <> 'generate_image'
               OR batch_action.action_index NOT BETWEEN 0 AND 9
               OR batch_action.status NOT IN ('queued','running'))
       )
       OR EXISTS (
           SELECT 1 FROM generate_series(0, action_count - 1) expected(index)
           WHERE NOT EXISTS (
               SELECT 1 FROM agent_actions actual
               WHERE actual.model_step_id = step.id
                 AND actual.action_index = expected.index
           )
       )
       OR NOT _agent_runtime_media_attempt_valid_v1(
           p_action_id, p_attempt_id, p_worker_id, p_execution_token,
           p_expected_attempt_version, p_request_hash
       )
       OR EXISTS (
           SELECT 1 FROM agent_runtime_provider_submission_facts provider_fact
           JOIN agent_actions provider_action ON provider_action.id = provider_fact.action_id
           WHERE provider_action.model_step_id = step.id
       ) THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_BATCH_SCOPE_INVALID'
            USING ERRCODE = '42501';
    END IF;

    manifest := _agent_runtime_media_input_manifest_v1(input_message.content);
    image_count := (manifest->>'image_count')::INTEGER;
    IF manifest->>'manifest_hash' IS DISTINCT FROM p_reference_manifest_hash THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_MANIFEST_CONFLICT'
            USING ERRCODE = '23505';
    END IF;

    FOR action IN SELECT * FROM agent_actions
      WHERE model_step_id = step.id ORDER BY action_index
    LOOP
        IF action.arguments ?| ARRAY[
            'task_id','user_id','org_id','credit_transaction_id','reserved_credits'
        ] THEN
            RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_INTERNAL_ARGUMENT_FORBIDDEN'
                USING ERRCODE = '22023';
        END IF;
        refs := COALESCE(action.arguments->'reference_image_indexes', '[]'::JSONB);
        IF jsonb_typeof(refs) IS DISTINCT FROM 'array'
           OR EXISTS (
               SELECT 1 FROM jsonb_array_elements(refs) ref
               WHERE jsonb_typeof(ref) <> 'number'
                  OR ref::TEXT !~ '^(0|[1-9][0-9]*)$'
                  OR (ref::TEXT)::INTEGER >= image_count
           )
           OR (SELECT count(*) FROM jsonb_array_elements(refs)) <>
              (SELECT count(DISTINCT ref::TEXT) FROM jsonb_array_elements(refs) ref) THEN
            RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_REFERENCE_INDEX_INVALID'
                USING ERRCODE = '22023';
        END IF;
        selected_model_id := NULLIF(btrim(action.arguments->>'model'), '');
        IF selected_model_id IS NULL THEN
            SELECT price.model_id INTO selected_model_id
              FROM agent_runtime_media_pricing_facts price
             WHERE pricing_revision = 'kie-image-pricing-v1'
               AND is_default_model AND active LIMIT 1;
        END IF;
        IF jsonb_array_length(refs) > 0
           AND selected_model_id LIKE '%text-to-image' THEN
            selected_model_id := replace(
                selected_model_id, 'text-to-image', 'image-to-image'
            );
        END IF;
        aspect_ratio := COALESCE(
            NULLIF(btrim(action.arguments->>'aspect_ratio'), ''), '1:1'
        );
        effective_resolution := NULLIF(btrim(action.arguments->>'resolution'), '');
        IF effective_resolution IS NOT NULL
           AND effective_resolution NOT IN ('1K','2K','4K') THEN
            effective_resolution := CASE effective_resolution
                WHEN '1024x1024' THEN '1K' WHEN '1024' THEN '1K'
                WHEN '2048x2048' THEN '2K' WHEN '2048' THEN '2K'
                WHEN '2560x1440' THEN '2K'
                WHEN '4096x4096' THEN '4K' WHEN '4096' THEN '4K'
                ELSE '1K' END;
        END IF;
        effective_resolution := COALESCE(effective_resolution, '1K');
        IF aspect_ratio = 'auto' AND effective_resolution <> '1K' THEN
            effective_resolution := '1K';
        ELSIF aspect_ratio = '1:1' AND effective_resolution = '4K' THEN
            effective_resolution := '2K';
        END IF;
        SELECT * INTO pricing FROM agent_runtime_media_pricing_facts price
         WHERE price.pricing_revision = 'kie-image-pricing-v1'
           AND price.model_id = selected_model_id AND price.active
           AND price.resolution_key = CASE
               WHEN price.supports_resolution THEN effective_resolution
               ELSE 'default' END;
        IF pricing.model_id IS NULL THEN
            RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_PRICING_UNAVAILABLE'
                USING ERRCODE = '22023';
        END IF;
        IF pricing.requires_image_input AND jsonb_array_length(refs) = 0 THEN
            RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_MODEL_INPUT_REQUIRED'
                USING ERRCODE = '22023';
        END IF;
        resolution_key := pricing.resolution_key;
        total_credits := total_credits + pricing.user_credits;
        items := items || jsonb_build_array(jsonb_build_object(
            'action_id', action.id, 'action_index', action.action_index,
            'task_id', action.id, 'model_id', selected_model_id,
            'action_arguments_hash', action.arguments_hash,
            'action_request_hash', action.request_hash,
            'resolution_key', resolution_key, 'unit_credits', pricing.user_credits,
            'pricing_fact_hash', pricing.fact_hash,
            'request_params', (action.arguments - 'model' - 'resolution') ||
                jsonb_build_object('model', selected_model_id) ||
                CASE WHEN resolution_key = 'default' THEN '{}'::JSONB
                     ELSE jsonb_build_object('resolution', resolution_key) END
        ));
        items := jsonb_set(
            items, ARRAY[(jsonb_array_length(items) - 1)::TEXT, 'provider_request_hash'],
            to_jsonb(encode(digest(convert_to(
                (items->(jsonb_array_length(items) - 1)->'request_params')::TEXT,
                'UTF8'
            ), 'sha256'), 'hex'))
        );
    END LOOP;

    SELECT count(*) INTO existing_count FROM agent_runtime_media_action_bindings
     WHERE model_step_id = step.id;
    IF existing_count = action_count THEN
        FOR item IN SELECT value FROM jsonb_array_elements(items) LOOP
            SELECT * INTO binding FROM agent_runtime_media_action_bindings
             WHERE action_id = (item->>'action_id')::UUID;
            IF binding.task_id IS DISTINCT FROM (item->>'task_id')::UUID
               OR binding.session_id IS DISTINCT FROM session.id
               OR binding.run_id IS DISTINCT FROM run.id
               OR binding.model_step_id IS DISTINCT FROM step.id
               OR binding.chat_task_id IS DISTINCT FROM chat_task.id
               OR binding.org_id IS DISTINCT FROM session.org_id
               OR binding.user_id IS DISTINCT FROM session.user_id
               OR binding.conversation_id IS DISTINCT FROM session.conversation_id
               OR binding.batch_hash IS DISTINCT FROM seed_action.batch_hash
               OR binding.action_index IS DISTINCT FROM (item->>'action_index')::INTEGER
               OR binding.action_arguments_hash IS DISTINCT FROM
                  item->>'action_arguments_hash'
               OR binding.action_request_hash IS DISTINCT FROM
                  item->>'action_request_hash'
               OR binding.input_message_id IS DISTINCT FROM input_message.id
               OR binding.output_message_id IS DISTINCT FROM output_message.id
               OR binding.pricing_model_id IS DISTINCT FROM item->>'model_id'
               OR binding.pricing_resolution IS DISTINCT FROM item->>'resolution_key'
               OR binding.pricing_fact_hash IS DISTINCT FROM item->>'pricing_fact_hash'
               OR binding.provider_request_hash IS DISTINCT FROM
                  item->>'provider_request_hash'
               OR binding.unit_credits IS DISTINCT FROM (item->>'unit_credits')::INTEGER
               OR binding.reference_manifest_hash IS DISTINCT FROM p_reference_manifest_hash THEN
                RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_PREPARE_CONFLICT'
                    USING ERRCODE = '23505';
            END IF;
        END LOOP;
        RETURN jsonb_build_object(
            'outcome', 'already_prepared', 'batch_hash', seed_action.batch_hash,
            'action_count', action_count, 'total_credits', total_credits,
            'binding', (SELECT to_jsonb(existing) FROM agent_runtime_media_action_bindings existing
                         WHERE existing.action_id = p_action_id)
        );
    ELSIF existing_count <> 0 THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_PREPARE_CONFLICT'
            USING ERRCODE = '23505';
    END IF;

    UPDATE users SET credits = credits - total_credits, updated_at = clock_timestamp()
     WHERE id = session.user_id AND status::TEXT = 'active' AND credits >= total_credits
     RETURNING credits INTO final_balance;
    IF final_balance IS NULL THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_INSUFFICIENT_CREDITS'
            USING ERRCODE = 'P0001';
    END IF;
    slots := (SELECT jsonb_agg(jsonb_build_object(
        'type', 'image', 'url', NULL, 'slot_id', value->>'action_id',
        'slot_index', (value->>'action_index')::INTEGER,
        'slot_status', 'pending', 'slot_revision', 0
    ) ORDER BY (value->>'action_index')::INTEGER) FROM jsonb_array_elements(items));
    UPDATE messages SET
        content = slots::TEXT,
        status = 'pending',
        generation_params = COALESCE(generation_params, '{}'::JSONB) || jsonb_build_object(
            'num_images', action_count,
            'runtime_media_batch', jsonb_build_object(
                'batch_hash', seed_action.batch_hash, 'slot_count', action_count,
                'projection_revision', 0
            )
        )
     WHERE id = output_message.id;

    FOR item IN SELECT value FROM jsonb_array_elements(items)
      ORDER BY (value->>'action_index')::INTEGER
    LOOP
        transaction_id := gen_random_uuid();
        INSERT INTO credit_transactions(
            id, task_id, user_id, amount, type, status, reason, org_id
        ) VALUES (
            transaction_id, (item->>'task_id')::UUID, session.user_id,
            (item->>'unit_credits')::INTEGER, 'lock', 'pending',
            'Agent Runtime media reservation', session.org_id
        );
        INSERT INTO tasks(
            id, client_task_id, user_id, org_id, conversation_id, type, status,
            credits_locked, credits_used, model_id, placeholder_message_id,
            assistant_message_id, request_params, placeholder_created_at,
            input_message_id, turn_id, base_context_revision,
            context_through_message_id, execution_mode, delivery_context,
            image_index, batch_id, credit_transaction_id
        ) VALUES (
            (item->>'task_id')::UUID, 'runtime-media:' || (item->>'action_id'),
            session.user_id, session.org_id, session.conversation_id, 'image',
            'preparing', (item->>'unit_credits')::INTEGER, 0, item->>'model_id',
            output_message.id::TEXT, output_message.id, item->'request_params',
            clock_timestamp(), input_message.id, chat_task.turn_id,
            chat_task.base_context_revision, chat_task.context_through_message_id,
            'serial', jsonb_build_object(
                'channel', COALESCE(run.capability_snapshot->>'channel', 'web'),
                'actor', FALSE, 'runtime', TRUE, 'runtime_owner', 'action_loop'
            ), (item->>'action_index')::INTEGER, seed_action.batch_hash, transaction_id
        );
        item_balance := final_balance + total_credits -
            (SELECT COALESCE(sum((prior->>'unit_credits')::INTEGER), 0)
             FROM jsonb_array_elements(items) prior
             WHERE (prior->>'action_index')::INTEGER <= (item->>'action_index')::INTEGER);
        INSERT INTO credits_history(
            user_id, change_type, change_amount, balance_after, description, org_id
        ) VALUES (
            session.user_id, 'image_generation_cost'::credits_change_type,
            -(item->>'unit_credits')::INTEGER, item_balance,
            'Agent Runtime media reservation', session.org_id
        );
        INSERT INTO agent_runtime_media_action_bindings(
            action_id, task_id, session_id, run_id, model_step_id, chat_task_id,
            org_id, user_id, conversation_id, batch_hash, action_index,
            action_arguments_hash, action_request_hash,
            input_message_id, output_message_id, credit_transaction_id,
            pricing_revision, pricing_model_id, pricing_resolution,
            pricing_fact_hash, provider_request_hash, unit_credits,
            reference_manifest_hash
        ) VALUES (
            (item->>'action_id')::UUID, (item->>'task_id')::UUID, session.id,
            run.id, step.id, chat_task.id, session.org_id, session.user_id,
            session.conversation_id, seed_action.batch_hash,
            (item->>'action_index')::INTEGER,
            item->>'action_arguments_hash', item->>'action_request_hash',
            input_message.id, output_message.id,
            transaction_id, 'kie-image-pricing-v1', item->>'model_id',
            item->>'resolution_key', item->>'pricing_fact_hash',
            item->>'provider_request_hash', (item->>'unit_credits')::INTEGER,
            p_reference_manifest_hash
        );
    END LOOP;
    RETURN jsonb_build_object(
        'outcome', 'prepared', 'batch_hash', seed_action.batch_hash,
        'action_count', action_count, 'total_credits', total_credits,
        'binding', (SELECT to_jsonb(created) FROM agent_runtime_media_action_bindings created
                     WHERE created.action_id = p_action_id)
    );
END $$;

CREATE FUNCTION read_agent_runtime_media_binding_v1(
    p_action_id UUID, p_attempt_id UUID, p_worker_id TEXT,
    p_execution_token UUID, p_expected_attempt_version BIGINT,
    p_request_hash TEXT
) RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    binding agent_runtime_media_action_bindings%ROWTYPE;
    task tasks%ROWTYPE;
BEGIN
    PERFORM _agent_runtime_media_worker_v1();
    IF NOT _agent_runtime_media_attempt_valid_v1(
        p_action_id, p_attempt_id, p_worker_id, p_execution_token,
        p_expected_attempt_version, p_request_hash
    ) THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_ATTEMPT_SCOPE_INVALID'
            USING ERRCODE = '42501';
    END IF;
    SELECT * INTO binding FROM agent_runtime_media_action_bindings
     WHERE action_id = p_action_id;
    IF binding.action_id IS NULL THEN
        RETURN jsonb_build_object('outcome', 'not_prepared');
    END IF;
    SELECT * INTO task FROM tasks WHERE id = binding.task_id;
    IF task.id IS NULL OR task.user_id IS DISTINCT FROM binding.user_id
       OR task.org_id IS DISTINCT FROM binding.org_id
       OR task.conversation_id IS DISTINCT FROM binding.conversation_id
       OR task.input_message_id IS DISTINCT FROM binding.input_message_id
       OR task.assistant_message_id IS DISTINCT FROM binding.output_message_id
       OR task.credit_transaction_id IS DISTINCT FROM binding.credit_transaction_id
       OR encode(digest(convert_to(task.request_params::TEXT, 'UTF8'), 'sha256'), 'hex')
          IS DISTINCT FROM binding.provider_request_hash
       OR task.delivery_context @> '{"actor":false,"runtime":true}'::JSONB IS NOT TRUE THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_BINDING_CONFLICT'
            USING ERRCODE = '23505';
    END IF;
    RETURN jsonb_build_object(
        'outcome', 'found', 'binding', to_jsonb(binding),
        'request_params', task.request_params
    );
END $$;

CREATE FUNCTION settle_agent_runtime_media_credit_v1(
    p_action_id UUID, p_projection_revision BIGINT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    binding agent_runtime_media_action_bindings%ROWTYPE;
BEGIN
    PERFORM _agent_runtime_media_projection_v1();
    SELECT * INTO binding FROM agent_runtime_media_action_bindings
     WHERE action_id = p_action_id FOR UPDATE;
    IF binding.action_id IS NULL THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
    IF p_projection_revision < binding.projection_revision THEN
        RETURN jsonb_build_object('outcome','stale_projection');
    END IF;
    IF binding.credit_state = 'confirmed' THEN
        RETURN jsonb_build_object('outcome','already_settled');
    END IF;
    IF binding.credit_state <> 'pending'
       OR NOT EXISTS (SELECT 1 FROM agent_actions action
                      WHERE action.id = p_action_id AND action.status = 'completed') THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_CREDIT_STATE_CONFLICT'
            USING ERRCODE = '55000';
    END IF;
    UPDATE credit_transactions SET status = 'confirmed', confirmed_at = clock_timestamp()
     WHERE id = binding.credit_transaction_id AND status = 'pending';
    IF NOT FOUND THEN RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_CREDIT_TRANSACTION_CONFLICT'; END IF;
    UPDATE agent_runtime_media_action_bindings SET
        credit_state = 'confirmed', projection_revision = p_projection_revision,
        state_version = state_version + 1, updated_at = clock_timestamp()
     WHERE action_id = p_action_id;
    RETURN jsonb_build_object('outcome','settled');
END $$;

CREATE FUNCTION refund_agent_runtime_media_credit_v1(
    p_action_id UUID, p_projection_revision BIGINT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    binding agent_runtime_media_action_bindings%ROWTYPE;
    refunded JSONB;
BEGIN
    PERFORM _agent_runtime_media_projection_v1();
    SELECT * INTO binding FROM agent_runtime_media_action_bindings
     WHERE action_id = p_action_id FOR UPDATE;
    IF binding.action_id IS NULL THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
    IF p_projection_revision < binding.projection_revision THEN
        RETURN jsonb_build_object('outcome','stale_projection');
    END IF;
    IF binding.credit_state = 'refunded' THEN
        RETURN jsonb_build_object('outcome','already_refunded');
    END IF;
    IF binding.credit_state <> 'pending'
       OR NOT EXISTS (SELECT 1 FROM agent_actions action WHERE action.id = p_action_id
                      AND action.status IN ('failed','rejected','cancelled')) THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_CREDIT_STATE_CONFLICT'
            USING ERRCODE = '55000';
    END IF;
    SELECT atomic_refund_credits(binding.credit_transaction_id) INTO refunded;
    IF COALESCE((refunded->>'refunded')::BOOLEAN, FALSE) IS NOT TRUE THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_CREDIT_REFUND_CONFLICT'
            USING ERRCODE = '55000';
    END IF;
    UPDATE agent_runtime_media_action_bindings SET
        credit_state = 'refunded', projection_revision = p_projection_revision,
        state_version = state_version + 1, updated_at = clock_timestamp()
     WHERE action_id = p_action_id;
    RETURN jsonb_build_object('outcome','refunded');
END $$;

CREATE OR REPLACE FUNCTION worker_discover_media_tasks(p_limit INTEGER DEFAULT 100)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_tasks JSONB;
BEGIN
    IF session_user <> 'everydayai_worker' THEN
        RAISE EXCEPTION 'MEDIA_WORKER_ROLE_SCOPE_MISMATCH' USING ERRCODE = '42501';
    END IF;
    IF p_limit IS NULL OR p_limit < 1 OR p_limit > 500 THEN
        RAISE EXCEPTION 'MEDIA_WORKER_LIMIT_INVALID' USING ERRCODE = '22023';
    END IF;
    SELECT COALESCE(jsonb_agg(to_jsonb(task_row)), '[]'::JSONB) INTO v_tasks
    FROM (
        SELECT task.* FROM tasks task
        WHERE task.status IN ('pending','running') AND task.type IN ('image','video')
          AND COALESCE((task.delivery_context->>'runtime')::BOOLEAN, FALSE) IS FALSE
          AND NOT EXISTS (
              SELECT 1 FROM agent_runtime_media_action_bindings binding
              WHERE binding.task_id = task.id
          )
          AND (task.org_id IS NULL OR EXISTS (
              SELECT 1 FROM organizations organization
              WHERE organization.id = task.org_id AND organization.status = 'active'
          ))
        ORDER BY COALESCE(task.last_polled_at, task.created_at), task.id
        LIMIT p_limit
    ) task_row;
    RETURN v_tasks;
END $$;

REVOKE ALL ON TABLE agent_runtime_media_pricing_facts,
    agent_runtime_media_action_bindings
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime, everydayai_worker,
    everydayai_sync, everydayai, everydayai_agent_runtime_worker,
    everydayai_projection_worker, everydayai_authorization_worker,
    everydayai_sandbox_worker, everydayai_runtime_admin;

REVOKE ALL ON FUNCTION _agent_runtime_media_pricing_immutable_v1(),
    _agent_runtime_media_worker_v1(), _agent_runtime_media_projection_v1(),
    _agent_runtime_media_input_manifest_v1(TEXT),
    _agent_runtime_media_attempt_valid_v1(UUID,UUID,TEXT,UUID,BIGINT,TEXT),
    read_agent_runtime_media_manifest_v1(UUID,UUID,TEXT,UUID,BIGINT,TEXT),
    prepare_agent_runtime_media_batch_v1(UUID,UUID,TEXT,UUID,BIGINT,TEXT,TEXT),
    read_agent_runtime_media_binding_v1(UUID,UUID,TEXT,UUID,BIGINT,TEXT),
    settle_agent_runtime_media_credit_v1(UUID,BIGINT),
    refund_agent_runtime_media_credit_v1(UUID,BIGINT)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime, everydayai_worker,
    everydayai_sync, everydayai, everydayai_agent_runtime_worker,
    everydayai_projection_worker, everydayai_authorization_worker,
    everydayai_sandbox_worker, everydayai_runtime_admin;

GRANT EXECUTE ON FUNCTION
    read_agent_runtime_media_manifest_v1(UUID,UUID,TEXT,UUID,BIGINT,TEXT),
    prepare_agent_runtime_media_batch_v1(UUID,UUID,TEXT,UUID,BIGINT,TEXT,TEXT),
    read_agent_runtime_media_binding_v1(UUID,UUID,TEXT,UUID,BIGINT,TEXT)
TO everydayai_agent_runtime_worker;
GRANT EXECUTE ON FUNCTION settle_agent_runtime_media_credit_v1(UUID,BIGINT),
    refund_agent_runtime_media_credit_v1(UUID,BIGINT)
TO everydayai_projection_worker;

REVOKE ALL ON FUNCTION worker_discover_media_tasks(INTEGER)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_agent_runtime_worker, everydayai_projection_worker,
    everydayai_authorization_worker, everydayai_sandbox_worker,
    everydayai_sync, everydayai, everydayai_runtime_admin;
GRANT EXECUTE ON FUNCTION worker_discover_media_tasks(INTEGER)
TO everydayai_worker;

RESET ROLE;
