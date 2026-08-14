/* Roll back 228.08g1 only after ModelLoop video Action projection is drained. */
SET LOCAL ROLE everydayai_owner;

DO $guard$
BEGIN
    IF to_regprocedure(
           '_agent_runtime_media_normalize_model_video_event_228_08g1_v1'
           '(agent_runtime_events)'
       ) IS NOT NULL THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_228_08I1_MUST_ROLL_BACK_FIRST'
            USING ERRCODE='55000';
    END IF;
    IF to_regprocedure(
           '_derive_agent_runtime_model_video_wecom_outbox_v1()'
       ) IS NOT NULL THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_228_08G2_MUST_ROLL_BACK_FIRST'
            USING ERRCODE='55000';
    END IF;
    IF EXISTS(
        SELECT 1 FROM agent_runtime_media_normalized_projection_inputs_v1
    ) THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_228_08G1_PROJECTION_IN_FLIGHT'
            USING ERRCODE='55000';
    END IF;
    IF EXISTS(
        SELECT 1
          FROM agent_runtime_prepared_media_action_bindings binding
          JOIN agent_actions action ON action.id=binding.action_id
          JOIN tasks child_task ON child_task.id=binding.task_id
         WHERE binding.media_kind='video'
           AND _agent_runtime_prepared_media_source_v1(binding.action_id)='model_loop'
           AND (
               action.status NOT IN ('completed','failed','rejected','cancelled')
               OR child_task.status NOT IN ('completed','failed','cancelled')
               OR binding.credit_state='pending'
               OR EXISTS(
                   SELECT 1 FROM agent_action_attempts attempt
                    WHERE attempt.action_id=action.id
                      AND attempt.status IN (
                          'claimed','dispatching','accepted','unknown'
                      )
               )
           )
    ) THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_228_08G1_MODEL_VIDEO_NOT_DRAINED'
            USING ERRCODE='55000';
    END IF;
    IF EXISTS(
        SELECT 1
          FROM agent_runtime_events event
          JOIN agent_actions action ON action.id=event.correlation_id
          JOIN agent_runtime_prepared_media_action_bindings binding
            ON binding.action_id=action.id
          JOIN agent_projection_outbox outbox ON outbox.event_id=event.id
         WHERE event.action_id IS NULL
           AND event.event_version=1
           AND event.event_type IN (
               'action.requested','action.accepted','action.unknown',
               'action.completed','action.failed','action.rejected',
               'action.cancelled','action.provider.accepted',
               'action.provider.unknown','action.completed_after_cancel',
               'action.failed_after_cancel'
           )
           AND action.tool_name='generate_video'
           AND binding.media_kind='video'
           AND _agent_runtime_prepared_media_source_v1(action.id)='model_loop'
           AND action.session_id=event.session_id
           AND action.run_id=event.run_id
           AND action.model_step_id=event.model_step_id
           AND action.org_id IS NOT DISTINCT FROM event.org_id
           AND action.user_id IS NOT DISTINCT FROM event.user_id
           AND binding.session_id=action.session_id
           AND binding.run_id=action.run_id
           AND binding.model_step_id=action.model_step_id
           AND binding.org_id IS NOT DISTINCT FROM action.org_id
           AND binding.user_id IS NOT DISTINCT FROM action.user_id
           AND (
               (event.event_type='action.requested' AND event.actor_type='model')
               OR (event.event_type='action.cancelled'
                   AND event.actor_type='system')
               OR (event.event_type IN (
                       'action.completed_after_cancel','action.failed_after_cancel'
                   ) AND event.actor_type='reconciler')
               OR (event.event_type NOT IN (
                       'action.requested','action.cancelled',
                       'action.completed_after_cancel','action.failed_after_cancel'
                   ) AND event.actor_type='executor')
           )
           AND outbox.projection_kind IN ('web_runtime','wecom')
           AND (
               outbox.status<>'delivered'
               OR NOT EXISTS(
                   SELECT 1
                     FROM agent_runtime_media_projection_results result
                    WHERE result.outbox_id=outbox.id
                      AND result.event_id=event.id
                      AND result.action_id=action.id
                      AND result.projection_action='action_progress'
               )
           )
    ) THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_228_08G1_EVENT_PROJECTION_NOT_DRAINED'
            USING ERRCODE='55000';
    END IF;
END
$guard$;

DROP TRIGGER agent_runtime_media_model_video_normalized_event_v1
    ON agent_runtime_media_projection_results;
DROP FUNCTION _project_agent_runtime_model_video_normalized_event_v1();

REVOKE ALL ON FUNCTION
    read_agent_runtime_media_projection_v1(UUID,UUID),
    apply_agent_runtime_media_projection_v1(UUID,UUID,TEXT,JSONB)
FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,
    everydayai_sync,everydayai,everydayai_agent_runtime_worker,
    everydayai_projection_worker,everydayai_authorization_worker,
    everydayai_sandbox_worker,everydayai_runtime_admin;
DROP FUNCTION apply_agent_runtime_media_projection_v1(UUID,UUID,TEXT,JSONB);
ALTER FUNCTION _apply_agent_runtime_media_projection_228_06_v1(
    UUID,UUID,TEXT,JSONB
) RENAME TO apply_agent_runtime_media_projection_v1;
DROP FUNCTION read_agent_runtime_media_projection_v1(UUID,UUID);
ALTER FUNCTION _read_agent_runtime_media_projection_228_06_v1(UUID,UUID)
    RENAME TO read_agent_runtime_media_projection_v1;
GRANT EXECUTE ON FUNCTION
    read_agent_runtime_media_projection_v1(UUID,UUID),
    apply_agent_runtime_media_projection_v1(UUID,UUID,TEXT,JSONB)
TO everydayai_projection_worker;

DROP FUNCTION _agent_runtime_media_projection_action_v1(agent_runtime_events);
ALTER FUNCTION _agent_runtime_media_projection_action_228_06_v1(
    agent_runtime_events
) RENAME TO _agent_runtime_media_projection_action_v1;
DROP FUNCTION _agent_runtime_media_normalize_model_video_event_v1(
    agent_runtime_events
);
DROP TABLE agent_runtime_media_normalized_projection_inputs_v1;

RESET ROLE;
