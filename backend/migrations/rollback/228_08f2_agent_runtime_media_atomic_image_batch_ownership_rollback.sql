/* Roll back 228.08f2 and restore the 228.08d caller contract. */
SET LOCAL ROLE everydayai_owner;

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
