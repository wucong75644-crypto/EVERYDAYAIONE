/* Roll back 228.08f2 and restore the 228.08d caller contract. */
SET LOCAL ROLE everydayai_owner;

DO $guard$
BEGIN
    IF to_regclass('public.agent_runtime_prepared_image_batch_slots') IS NULL
       OR to_regprocedure(
           '_merge_agent_runtime_prepared_image_batch_projection_v1(uuid,jsonb)'
       ) IS NULL
       OR NOT EXISTS (
           SELECT 1 FROM pg_trigger
            WHERE tgrelid='agent_runtime_media_projection_results'::regclass
              AND tgname='agent_runtime_prepared_image_batch_result_v1'
              AND NOT tgisinternal
       ) THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_IMAGE_BATCH_ROLLBACK_ORDER_INVALID'
            USING ERRCODE='55000';
    END IF;
END
$guard$;

LOCK TABLE tasks IN EXCLUSIVE MODE;

DO $guard$
BEGIN
    IF EXISTS (
        WITH task_ownership AS (
            SELECT task.org_id,task.user_id,task.conversation_id,
                   task.input_message_id,task.assistant_message_id,task.batch_id,
                   task.delivery_context->>'channel' AS channel,
                   message.generation_params->>'type' AS generation_type,
                   task.image_index,
                   (
                       binding.action_id IS NOT NULL
                       OR task.delivery_context->>'runtime'='true'
                       OR idempotency.action_id IS NOT NULL
                   ) AS has_evidence,
                   (
                       binding.action_id IS NOT NULL
                       AND idempotency.action_id=binding.action_id
                       AND idempotency.run_id=binding.run_id
                       AND idempotency.model_step_id=binding.model_step_id
                       AND binding.task_id=task.id
                       AND binding.org_id IS NOT DISTINCT FROM task.org_id
                       AND binding.user_id=task.user_id
                       AND binding.conversation_id=task.conversation_id
                       AND binding.input_message_id=task.input_message_id
                       AND binding.output_message_id=task.assistant_message_id
                       AND binding.media_kind='image'
                       AND message.generation_params->>'type'='image'
                       AND task.image_index BETWEEN 0 AND 9
                       AND idempotency.tool_name='generate_image'
                       AND idempotency.stable_tool_call_id=task.id::TEXT
                       AND NULLIF(btrim(idempotency.idempotency_key),'') IS NOT NULL
                       AND length(btrim(idempotency.idempotency_key))<=200
                       AND task.delivery_context @> jsonb_build_object(
                           'actor',FALSE,'runtime',TRUE,
                           'runtime_action_id',binding.action_id::TEXT
                       )
                       AND task.delivery_context->>'channel'='web'
                   ) AS is_valid
              FROM tasks task
              JOIN messages message ON message.id=task.assistant_message_id
              LEFT JOIN agent_runtime_prepared_media_action_bindings binding
                ON binding.task_id=task.id
              LEFT JOIN LATERAL (
                  SELECT action.id AS action_id,run.id AS run_id,
                         action.model_step_id,action.tool_name,
                         action.stable_tool_call_id,command.idempotency_key
                    FROM agent_runtime_sessions session
                    JOIN agent_session_commands command
                      ON command.session_id=session.id
                     AND command.payload->>'task_id'=task.id::TEXT
                    JOIN agent_runs run ON run.command_id=command.id
                    JOIN agent_actions action ON action.run_id=run.id
                   WHERE session.conversation_id=task.conversation_id
                     AND session.org_id IS NOT DISTINCT FROM task.org_id
                     AND session.user_id=task.user_id
                     AND command.command_type='submit_input'
                     AND run.session_id=session.id
                     AND run.org_id IS NOT DISTINCT FROM session.org_id
                     AND run.user_id=session.user_id
                     AND action.session_id=session.id
                     AND action.run_id=run.id
                     AND action.org_id IS NOT DISTINCT FROM session.org_id
                     AND action.user_id=session.user_id
                   ORDER BY (action.id=binding.action_id) DESC,
                            action.action_index,action.id
                   LIMIT 1
              ) idempotency ON TRUE
             WHERE task.type::TEXT='image'
               AND task.batch_id IS NOT NULL
        ), batch_ownership AS (
            SELECT org_id,user_id,conversation_id,input_message_id,
                   assistant_message_id,batch_id,count(*) AS total_count,
                   count(*) FILTER(WHERE has_evidence) AS evidence_count,
                   count(*) FILTER(WHERE is_valid) AS valid_count,
                   count(DISTINCT image_index) AS index_count
              FROM task_ownership
             GROUP BY org_id,user_id,conversation_id,input_message_id,
                      assistant_message_id,batch_id
            HAVING count(*)>1
               AND bool_or(
                   generation_type='image' OR channel='web' OR has_evidence
               )
        )
        SELECT 1 FROM batch_ownership
         WHERE evidence_count>0
           AND (
               evidence_count<>total_count
               OR valid_count<>total_count
               OR index_count<>total_count
               OR total_count NOT BETWEEN 2 AND 10
           )
    ) THEN
        RAISE EXCEPTION
            'AGENT_RUNTIME_MEDIA_IMAGE_BATCH_ROLLBACK_PARTIAL_OWNERSHIP'
            USING ERRCODE='55000';
    END IF;
END
$guard$;

REVOKE ALL ON FUNCTION submit_agent_runtime_media_image_batch_v2(
    UUID,UUID,UUID,TEXT,TEXT,UUID,TEXT,TEXT,UUID,UUID,UUID,
    TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,JSONB
) FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,
    everydayai_worker,everydayai_sync,everydayai,
    everydayai_agent_runtime_worker,everydayai_projection_worker,
    everydayai_authorization_worker,everydayai_sandbox_worker,
    everydayai_runtime_admin;
DROP FUNCTION submit_agent_runtime_media_image_batch_v2(
    UUID,UUID,UUID,TEXT,TEXT,UUID,TEXT,TEXT,UUID,UUID,UUID,
    TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,JSONB
);
DROP FUNCTION _agent_runtime_media_image_batch_ownership_v1(
    UUID,UUID,UUID,UUID,UUID,TEXT,TEXT,JSONB
);
GRANT EXECUTE ON FUNCTION submit_agent_runtime_media_image_batch_v1(
    UUID,UUID,UUID,TEXT,TEXT,UUID,TEXT,TEXT,UUID,UUID,UUID,
    TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,JSONB
) TO everydayai_runtime,everydayai_wecom_runtime;

RESET ROLE;
