/* Roll back 228.08f1 only after every owned Web image batch is drained. */
SET LOCAL ROLE everydayai_owner;

DO $guard$
BEGIN
    IF to_regprocedure(
           'submit_agent_runtime_media_image_batch_v2(uuid,uuid,uuid,text,text,uuid,'
           'text,text,uuid,uuid,uuid,text,text,text,text,text,text,jsonb)'
       ) IS NOT NULL
       OR to_regprocedure(
           '_agent_runtime_media_image_batch_ownership_v1(uuid,uuid,uuid,uuid,uuid,'
           'text,text,jsonb)'
       ) IS NOT NULL THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_IMAGE_BATCH_ROLLBACK_08F2_REQUIRED'
            USING ERRCODE='55000';
    END IF;
END
$guard$;

LOCK TABLE tasks IN EXCLUSIVE MODE;

DO $guard$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM (
              SELECT task.org_id,task.user_id,task.conversation_id,
                     task.input_message_id,task.assistant_message_id,task.batch_id,
                     count(*) AS total_count,
                     count(*) FILTER(WHERE
                         binding.action_id IS NOT NULL
                         OR task.delivery_context->>'runtime'='true'
                     ) AS ownership_count,
                     bool_and(
                         binding.action_id IS NOT NULL
                         AND action.id=binding.action_id
                         AND binding.task_id=task.id
                         AND binding.org_id IS NOT DISTINCT FROM task.org_id
                         AND binding.user_id=task.user_id
                         AND binding.conversation_id=task.conversation_id
                         AND binding.input_message_id=task.input_message_id
                         AND binding.output_message_id=task.assistant_message_id
                         AND binding.media_kind='image'
                         AND message.generation_params->>'type'='image'
                         AND task.credit_transaction_id=binding.credit_transaction_id
                         AND task.credits_locked=0
                         AND (
                             (
                                 task.status::TEXT='completed'
                                 AND binding.credit_state='confirmed'
                                 AND credit.status='confirmed'
                             ) OR (
                                 task.status::TEXT IN ('failed','cancelled')
                                 AND binding.credit_state='refunded'
                                 AND credit.status='refunded'
                             )
                         )
                         AND task.delivery_context @> jsonb_build_object(
                             'actor',FALSE,'runtime',TRUE,
                             'runtime_action_id',binding.action_id::TEXT
                         )
                         AND task.delivery_context->>'channel'='web'
                         AND task.status::TEXT IN ('completed','failed','cancelled')
                         AND action.status IN (
                             'completed','failed','rejected','cancelled'
                         )
                         AND binding.projection_revision>0
                         AND slot.action_id=binding.action_id
                         AND slot.task_id=task.id
                         AND slot.slot_index=task.image_index
                         AND slot.slot_status IN ('completed','failed','cancelled')
                         AND NOT EXISTS (
                             SELECT 1 FROM agent_action_attempts attempt
                              WHERE attempt.action_id=binding.action_id
                                AND attempt.status IN (
                                    'claimed','dispatching','accepted','unknown'
                                )
                         )
                         AND NOT EXISTS (
                             SELECT 1
                               FROM agent_runtime_events event
                               JOIN agent_projection_outbox outbox
                                 ON outbox.event_id=event.id
                              WHERE outbox.projection_kind IN ('web_runtime','wecom')
                                AND outbox.status<>'delivered'
                                AND (
                                    event.action_id=binding.action_id
                                    OR event.run_id=binding.run_id
                                )
                         )
                         AND EXISTS (
                             SELECT 1
                               FROM jsonb_array_elements(message.content::JSONB) part
                              WHERE part->>'slot_id'=binding.action_id::TEXT
                                AND (part->>'slot_index')::INTEGER=task.image_index
                                AND part->>'slot_status' IN (
                                    'completed','failed','cancelled'
                                )
                         )
                     ) AS safely_drained,
                     message.status::TEXT AS message_status,
                     CASE
                         WHEN jsonb_typeof(message.content::JSONB)='array'
                         THEN jsonb_array_length(message.content::JSONB)
                         ELSE -1
                     END AS content_count
                FROM tasks task
                JOIN messages message ON message.id=task.assistant_message_id
                LEFT JOIN agent_runtime_prepared_media_action_bindings binding
                  ON binding.task_id=task.id
                LEFT JOIN agent_actions action ON action.id=binding.action_id
                LEFT JOIN credit_transactions credit
                  ON credit.id=binding.credit_transaction_id
                LEFT JOIN agent_runtime_prepared_image_batch_slots slot
                  ON slot.action_id=binding.action_id
               WHERE task.type::TEXT='image'
                 AND task.batch_id IS NOT NULL
               GROUP BY task.org_id,task.user_id,task.conversation_id,
                        task.input_message_id,task.assistant_message_id,task.batch_id,
                        message.status,message.content
              HAVING count(*)>1
                 AND bool_or(
                     task.delivery_context->>'channel'='web'
                     OR binding.action_id IS NOT NULL
                     OR task.delivery_context->>'runtime'='true'
                 )
          ) batch
         WHERE batch.ownership_count>0
           AND NOT (
               batch.ownership_count=batch.total_count
               AND batch.safely_drained
               AND batch.message_status IN ('completed','failed')
               AND batch.content_count=batch.total_count
           )
    ) THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_IMAGE_BATCH_PROJECTION_NOT_DRAINED'
            USING ERRCODE='55000';
    END IF;
END
$guard$;

DROP TRIGGER agent_runtime_prepared_image_batch_result_v1
    ON agent_runtime_media_projection_results;
DROP FUNCTION _project_agent_runtime_prepared_image_batch_result_v1();
DROP FUNCTION _merge_agent_runtime_prepared_image_batch_projection_v1(UUID,JSONB);
DROP TABLE agent_runtime_prepared_image_batch_slots;

RESET ROLE;
