/* Roll back 228.08f1 before any ordinary Web image batch was projected. */
SET LOCAL ROLE everydayai_owner;

DO $guard$
BEGIN
    IF EXISTS(SELECT 1 FROM agent_runtime_prepared_image_batch_slots) THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_IMAGE_BATCH_PROJECTION_IN_USE'
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
