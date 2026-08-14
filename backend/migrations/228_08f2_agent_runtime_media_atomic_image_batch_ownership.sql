/* 228.08f2: preflight prepared-image batch ownership before atomic adoption. */
SET LOCAL ROLE everydayai_owner;

DO $guard$
BEGIN
    IF to_regprocedure(
           'submit_agent_runtime_media_image_batch_v1(uuid,uuid,uuid,text,text,uuid,'
           'text,text,uuid,uuid,uuid,text,text,text,text,text,text,jsonb)'
       ) IS NULL
       OR to_regclass('public.agent_runtime_prepared_image_batch_slots') IS NULL THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_IMAGE_BATCH_228_08F1_REQUIRED'
            USING ERRCODE='55000';
    END IF;
    IF to_regprocedure(
           'submit_agent_runtime_media_image_batch_v2(uuid,uuid,uuid,text,text,uuid,'
           'text,text,uuid,uuid,uuid,text,text,text,text,text,text,jsonb)'
       ) IS NOT NULL
       OR to_regprocedure(
           '_agent_runtime_media_image_batch_ownership_v1(uuid,uuid,uuid,uuid,uuid,'
           'text,text,jsonb)'
       ) IS NOT NULL THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_IMAGE_BATCH_OWNERSHIP_IDENTITY_CONFLICT'
            USING ERRCODE='55000';
    END IF;
    IF EXISTS (
        SELECT 1
          FROM tasks task
          JOIN messages message ON message.id=task.assistant_message_id
          LEFT JOIN agent_runtime_prepared_media_action_bindings binding
            ON binding.task_id=task.id
         WHERE task.type::TEXT='image'
           AND task.batch_id IS NOT NULL
           AND task.delivery_context->>'channel'='web'
           AND message.generation_params->>'type'='image'
         GROUP BY task.org_id,task.user_id,task.conversation_id,
                  task.input_message_id,task.assistant_message_id,task.batch_id
        HAVING count(*)>1
           AND (
               count(binding.action_id)>0
               OR bool_or(task.delivery_context->>'runtime'='true')
           )
           AND NOT (
               count(binding.action_id)=count(*)
               AND bool_and(
                   task.delivery_context @> jsonb_build_object(
                       'actor',FALSE,'runtime',TRUE,
                       'runtime_action_id',binding.action_id::TEXT
                   )
               )
           )
    ) THEN
        RAISE EXCEPTION
            'AGENT_RUNTIME_MEDIA_IMAGE_BATCH_PARTIAL_OWNERSHIP_RECONCILE_REQUIRED'
            USING ERRCODE='55000';
    END IF;
END
$guard$;

CREATE FUNCTION _agent_runtime_media_image_batch_ownership_v1(
    p_conversation_id UUID,
    p_org_id UUID,
    p_user_id UUID,
    p_input_message_id UUID,
    p_output_message_id UUID,
    p_batch_id TEXT,
    p_model_id TEXT,
    p_items JSONB
) RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path=pg_catalog,public
AS $$
DECLARE
    item_count INTEGER;
    locked_count INTEGER:=0;
    batch_task_count INTEGER;
    evidence_count INTEGER;
    valid_count INTEGER;
    locked_task_id UUID;
    results JSONB;
    output_message messages%ROWTYPE;
BEGIN
    PERFORM _assert_agent_runtime_actor(FALSE);
    IF jsonb_typeof(p_items) IS DISTINCT FROM 'array'
       OR jsonb_array_length(p_items) NOT BETWEEN 1 AND 10
       OR NULLIF(btrim(COALESCE(p_batch_id,'')),'') IS NULL
       OR length(btrim(p_batch_id))>200
       OR p_input_message_id IS NULL OR p_output_message_id IS NULL
       OR NULLIF(btrim(COALESCE(p_model_id,'')),'') IS NULL THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_IMAGE_BATCH_INVALID'
            USING ERRCODE='22023';
    END IF;
    item_count:=jsonb_array_length(p_items);
    IF EXISTS(
        SELECT 1 FROM jsonb_array_elements(p_items) input(item)
         WHERE jsonb_typeof(input.item) IS DISTINCT FROM 'object'
            OR NULLIF(btrim(input.item->>'task_id'),'') IS NULL
            OR NULLIF(btrim(input.item->>'idempotency_key'),'') IS NULL
            OR length(btrim(input.item->>'idempotency_key'))>200
            OR jsonb_typeof(input.item->'arguments') IS DISTINCT FROM 'object'
            OR input.item->'arguments'->>'model' IS DISTINCT FROM p_model_id
    ) OR (
        SELECT count(DISTINCT input.item->>'task_id')
          FROM jsonb_array_elements(p_items) input(item)
    )<>item_count OR (
        SELECT count(DISTINCT input.item->>'idempotency_key')
          FROM jsonb_array_elements(p_items) input(item)
    )<>item_count THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_IMAGE_BATCH_INVALID'
            USING ERRCODE='22023';
    END IF;

    FOR locked_task_id IN
        SELECT task.id
          FROM tasks task
          JOIN jsonb_array_elements(p_items) input(item)
            ON task.id=(input.item->>'task_id')::UUID
         WHERE task.conversation_id=p_conversation_id
           AND task.user_id=p_user_id
           AND task.org_id IS NOT DISTINCT FROM p_org_id
           AND task.type::TEXT='image'
           AND task.input_message_id=p_input_message_id
           AND task.assistant_message_id=p_output_message_id
           AND task.batch_id IS NOT DISTINCT FROM p_batch_id
           AND task.model_id IS NOT DISTINCT FROM p_model_id
           AND task.delivery_context->>'channel'='web'
         ORDER BY task.id
         FOR UPDATE OF task
    LOOP
        locked_count:=locked_count+1;
    END LOOP;
    IF locked_count<>item_count THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_IMAGE_BATCH_SCOPE_MISMATCH'
            USING ERRCODE='42501';
    END IF;
    SELECT * INTO output_message FROM messages
     WHERE id=p_output_message_id FOR UPDATE;
    IF output_message.id IS NULL
       OR output_message.conversation_id IS DISTINCT FROM p_conversation_id
       OR output_message.org_id IS DISTINCT FROM p_org_id
       OR output_message.role::TEXT<>'assistant'
       OR output_message.generation_params->>'type'<>'image' THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_IMAGE_BATCH_SCOPE_MISMATCH'
            USING ERRCODE='42501';
    END IF;
    SELECT count(*) INTO batch_task_count
      FROM tasks task
     WHERE task.conversation_id=p_conversation_id
       AND task.user_id=p_user_id
       AND task.org_id IS NOT DISTINCT FROM p_org_id
       AND task.type::TEXT='image'
       AND task.input_message_id=p_input_message_id
       AND task.assistant_message_id=p_output_message_id
       AND task.batch_id IS NOT DISTINCT FROM p_batch_id;
    IF batch_task_count<>item_count THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_IMAGE_BATCH_SCOPE_MISMATCH'
            USING ERRCODE='42501';
    END IF;

    WITH requested AS (
        SELECT input.item,input.ordinality
          FROM jsonb_array_elements(p_items) WITH ORDINALITY input(item,ordinality)
    ), facts AS (
        SELECT requested.item,requested.ordinality,task.id AS task_id,
               task.delivery_context,
               binding.action_id AS binding_action_id,
               binding.task_id AS binding_task_id,
               binding.run_id AS binding_run_id,
               binding.model_step_id AS binding_model_step_id,
               binding.org_id AS binding_org_id,binding.user_id AS binding_user_id,
               binding.conversation_id AS binding_conversation_id,
               binding.input_message_id AS binding_input_message_id,
               binding.output_message_id AS binding_output_message_id,
               binding.media_kind,owned.action_id,owned.run_id,owned.model_step_id
          FROM requested
          JOIN tasks task ON task.id=(requested.item->>'task_id')::UUID
          LEFT JOIN agent_runtime_prepared_media_action_bindings binding
            ON binding.task_id=task.id
          LEFT JOIN LATERAL (
              SELECT action.id AS action_id,run.id AS run_id,
                     action.model_step_id
                FROM agent_runtime_sessions session
                JOIN agent_session_commands command
                  ON command.session_id=session.id
                 AND command.idempotency_key=btrim(requested.item->>'idempotency_key')
                JOIN agent_runs run ON run.command_id=command.id
                JOIN agent_actions action ON action.run_id=run.id
               WHERE session.conversation_id=p_conversation_id
                 AND session.org_id IS NOT DISTINCT FROM p_org_id
                 AND session.user_id=p_user_id
               ORDER BY action.action_index,action.id
               LIMIT 1
          ) owned ON TRUE
    ), classified AS (
        SELECT facts.*,
               (
                   binding_action_id IS NOT NULL
                   OR delivery_context->>'runtime'='true'
                   OR facts.action_id IS NOT NULL
               ) AS has_evidence,
               (
                   binding_action_id IS NOT NULL
                   AND facts.action_id=binding_action_id
                   AND facts.run_id=binding_run_id
                   AND facts.model_step_id=binding_model_step_id
                   AND binding_task_id=task_id
                   AND binding_org_id IS NOT DISTINCT FROM p_org_id
                   AND binding_user_id=p_user_id
                   AND binding_conversation_id=p_conversation_id
                   AND binding_input_message_id=p_input_message_id
                   AND binding_output_message_id=p_output_message_id
                   AND media_kind='image'
                   AND delivery_context @> jsonb_build_object(
                       'actor',FALSE,'runtime',TRUE,
                       'runtime_action_id',binding_action_id::TEXT
                   )
               ) AS is_valid
          FROM facts
    )
    SELECT count(*) FILTER(WHERE has_evidence),
           count(*) FILTER(WHERE is_valid),
           COALESCE(jsonb_agg(
               jsonb_build_object(
                   'task_id',item->>'task_id','outcome','already_exists',
                   'action_id',action_id,'run_id',run_id,
                   'model_step_id',model_step_id,'runtime_owned',TRUE
               ) ORDER BY ordinality
           ) FILTER(WHERE is_valid),'[]'::JSONB)
      INTO evidence_count,valid_count,results
      FROM classified;
    IF evidence_count=0 THEN
        RETURN jsonb_build_object('ownership','none');
    END IF;
    IF evidence_count=item_count AND valid_count=item_count THEN
        RETURN jsonb_build_object('ownership','full','results',results);
    END IF;
    RETURN jsonb_build_object(
        'ownership','partial','evidence_count',evidence_count,
        'valid_count',valid_count,'total_count',item_count,'results',results
    );
END;
$$;

CREATE FUNCTION submit_agent_runtime_media_image_batch_v2(
    p_conversation_id UUID,p_org_id UUID,p_user_id UUID,p_scope_kind TEXT,
    p_scope_id TEXT,p_created_by_user_id UUID,p_agent_definition_id TEXT,
    p_agent_definition_revision TEXT,p_input_message_id UUID,
    p_output_message_id UUID,p_turn_id UUID,p_batch_id TEXT,p_model_id TEXT,
    p_model_provider TEXT,p_model_revision TEXT,p_catalog_revision TEXT,
    p_policy_revision TEXT,p_items JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE ownership JSONB;
BEGIN
    PERFORM _assert_agent_runtime_actor(FALSE);
    ownership:=_agent_runtime_media_image_batch_ownership_v1(
        p_conversation_id,p_org_id,p_user_id,p_input_message_id,
        p_output_message_id,p_batch_id,p_model_id,p_items
    );
    IF ownership->>'ownership'='none' THEN
        RETURN submit_agent_runtime_media_image_batch_v1(
            p_conversation_id,p_org_id,p_user_id,p_scope_kind,p_scope_id,
            p_created_by_user_id,p_agent_definition_id,
            p_agent_definition_revision,p_input_message_id,p_output_message_id,
            p_turn_id,p_batch_id,p_model_id,p_model_provider,p_model_revision,
            p_catalog_revision,p_policy_revision,p_items
        );
    END IF;
    IF ownership->>'ownership'='full' THEN
        RETURN jsonb_build_object(
            'outcome','already_exists','runtime_owned',TRUE,
            'results',ownership->'results'
        );
    END IF;
    RETURN jsonb_build_object(
        'outcome','partial_ownership','runtime_owned',FALSE,
        'results',ownership->'results',
        'evidence_count',ownership->'evidence_count',
        'valid_count',ownership->'valid_count',
        'total_count',ownership->'total_count'
    );
END;
$$;

REVOKE ALL ON FUNCTION submit_agent_runtime_media_image_batch_v1(
    UUID,UUID,UUID,TEXT,TEXT,UUID,TEXT,TEXT,UUID,UUID,UUID,
    TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,JSONB
) FROM everydayai_runtime,everydayai_wecom_runtime;
REVOKE ALL ON FUNCTION
    _agent_runtime_media_image_batch_ownership_v1(
        UUID,UUID,UUID,UUID,UUID,TEXT,TEXT,JSONB
    ),
    submit_agent_runtime_media_image_batch_v2(
        UUID,UUID,UUID,TEXT,TEXT,UUID,TEXT,TEXT,UUID,UUID,UUID,
        TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,JSONB
    )
FROM PUBLIC,everydayai_worker,everydayai_sync,everydayai,
    everydayai_agent_runtime_worker,everydayai_projection_worker,
    everydayai_authorization_worker,everydayai_sandbox_worker,
    everydayai_runtime_admin,everydayai_runtime,everydayai_wecom_runtime;
GRANT EXECUTE ON FUNCTION submit_agent_runtime_media_image_batch_v2(
    UUID,UUID,UUID,TEXT,TEXT,UUID,TEXT,TEXT,UUID,UUID,UUID,
    TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,JSONB
) TO everydayai_runtime,everydayai_wecom_runtime;

RESET ROLE;
