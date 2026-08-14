/* Roll back 228.08e2 only before any ModelLoop video binding exists. */
SET LOCAL ROLE everydayai_owner;

DO $guard$
BEGIN
    IF to_regprocedure(
           '_agent_runtime_media_normalize_model_video_event_v1(agent_runtime_events)'
       ) IS NOT NULL THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_228_08G1_MUST_ROLL_BACK_FIRST'
            USING ERRCODE='55000';
    END IF;
    IF EXISTS (
        SELECT 1 FROM agent_runtime_prepared_media_action_bindings binding
         WHERE _agent_runtime_prepared_media_source_v1(binding.action_id)='model_loop'
    ) THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_228_08E2_ACTIVE_MODEL_VIDEO_FACTS'
            USING ERRCODE='55000';
    END IF;
END
$guard$;

DROP TRIGGER agent_runtime_media_model_video_wecom_delivery_v1
    ON agent_runtime_media_projection_results;
DROP TRIGGER agent_runtime_media_wecom_delivery_v1
    ON agent_runtime_media_projection_results;
CREATE TRIGGER agent_runtime_media_wecom_delivery_v1
AFTER INSERT ON agent_runtime_media_projection_results
FOR EACH ROW WHEN (NEW.projection_kind='wecom')
EXECUTE FUNCTION _project_agent_runtime_media_wecom_delivery_v1();
DROP FUNCTION _project_agent_runtime_model_video_wecom_v1();
DROP FUNCTION _agent_runtime_media_model_video_event_v1(UUID);

DROP TRIGGER agent_runtime_media_model_video_run_projection_v1
    ON agent_runtime_media_projection_results;
DROP FUNCTION _project_agent_runtime_model_video_run_v1();
DROP FUNCTION _agent_runtime_media_model_video_run_projection_v1(
    agent_runtime_events,TEXT
);

DROP FUNCTION _agent_runtime_media_prepared_action_projection_v1(
    agent_runtime_events,JSONB
);
ALTER FUNCTION _agent_runtime_media_prepared_action_projection_228_06_v1(
    agent_runtime_events,JSONB
) RENAME TO _agent_runtime_media_prepared_action_projection_v1;
DROP FUNCTION _agent_runtime_media_model_video_action_projection_v1(
    agent_runtime_events,JSONB
);
DROP FUNCTION _agent_runtime_prepared_media_source_v1(UUID);

RESET ROLE;
