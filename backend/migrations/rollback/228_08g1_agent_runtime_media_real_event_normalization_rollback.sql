/* Roll back 228.08g1 after 228.08g2 and before projected logical events exist. */
SET LOCAL ROLE everydayai_owner;

DO $guard$
BEGIN
    IF to_regprocedure(
           '_derive_agent_runtime_model_video_wecom_outbox_v1()'
       ) IS NOT NULL THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_228_08G2_MUST_ROLL_BACK_FIRST'
            USING ERRCODE='55000';
    END IF;
    IF EXISTS(
        SELECT 1
          FROM agent_runtime_media_projection_results result
          JOIN agent_runtime_events event ON event.id=result.event_id
          JOIN agent_runtime_prepared_media_action_bindings binding
            ON binding.action_id=result.action_id
         WHERE event.action_id IS NULL
           AND event.correlation_id=result.action_id
           AND _agent_runtime_prepared_media_source_v1(binding.action_id)='model_loop'
    ) OR EXISTS(
        SELECT 1 FROM agent_runtime_media_normalized_projection_inputs_v1
    ) THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_228_08G1_NORMALIZED_EVENTS_IN_USE'
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
