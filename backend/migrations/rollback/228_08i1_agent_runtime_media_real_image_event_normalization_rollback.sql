/* Roll back 228.08i1 only after real image events are fully projected. */
SET LOCAL ROLE everydayai_owner;

DO $guard$
BEGIN
    IF to_regprocedure(
           '_derive_agent_runtime_model_media_wecom_outbox_v2()'
       ) IS NOT NULL THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_228_08I2_MUST_ROLL_BACK_FIRST'
            USING ERRCODE='55000';
    END IF;
    IF EXISTS(
        SELECT 1
          FROM agent_runtime_media_normalized_projection_inputs_v1 input
          JOIN agent_actions action ON action.id=input.action_id
         WHERE action.tool_name='generate_image'
    ) OR EXISTS(
        SELECT 1 FROM agent_actions action
         WHERE action.tool_name='generate_image'
           AND (EXISTS(
                   SELECT 1 FROM agent_runtime_media_action_bindings binding
                    WHERE binding.action_id=action.id
               ) OR EXISTS(
                   SELECT 1
                     FROM agent_runtime_prepared_media_action_bindings binding
                    WHERE binding.action_id=action.id
               ))
           AND (action.status NOT IN ('completed','failed','rejected','cancelled')
                OR EXISTS(
                    SELECT 1 FROM agent_action_attempts attempt
                     WHERE attempt.action_id=action.id
                       AND attempt.status IN(
                           'claimed','dispatching','accepted','unknown'
                       )
                ))
    ) THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_228_08I1_IMAGE_EVENTS_NOT_DRAINED'
            USING ERRCODE='55000';
    END IF;
    IF EXISTS(
        SELECT 1
          FROM agent_runtime_events event
          JOIN agent_actions action ON action.id=event.correlation_id
          JOIN agent_projection_outbox outbox ON outbox.event_id=event.id
         WHERE event.action_id IS NULL AND action.tool_name='generate_image'
           AND event.event_type IN(
               'action.requested','action.accepted','action.unknown',
               'action.completed','action.failed','action.rejected',
               'action.cancelled','action.provider.accepted',
               'action.provider.unknown','action.completed_after_cancel',
               'action.failed_after_cancel'
           )
           AND outbox.projection_kind IN ('web_runtime','wecom')
           AND (outbox.status<>'delivered' OR NOT EXISTS(
               SELECT 1 FROM agent_runtime_media_projection_results result
                WHERE result.outbox_id=outbox.id AND result.action_id=action.id
           ))
    ) THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_228_08I1_IMAGE_PROJECTION_NOT_DRAINED'
            USING ERRCODE='55000';
    END IF;
END
$guard$;

DROP TRIGGER agent_runtime_media_image_zbatch_result_v1
    ON agent_runtime_media_projection_results;
DROP FUNCTION _project_agent_runtime_normalized_image_batch_v1();
DROP FUNCTION _merge_agent_runtime_normalized_prepared_image_batch_v1(
    agent_runtime_events,JSONB
);
DROP TRIGGER agent_runtime_media_image_normalized_event_v1
    ON agent_runtime_media_projection_results;
DROP FUNCTION _project_agent_runtime_image_normalized_event_v1();
DROP FUNCTION _agent_runtime_media_normalize_model_video_event_v1(
    agent_runtime_events
);
ALTER FUNCTION _agent_runtime_media_normalize_model_video_event_228_08g1_v1(
    agent_runtime_events
) RENAME TO _agent_runtime_media_normalize_model_video_event_v1;
DROP FUNCTION _agent_runtime_media_normalize_image_event_v1(
    agent_runtime_events
);

RESET ROLE;
