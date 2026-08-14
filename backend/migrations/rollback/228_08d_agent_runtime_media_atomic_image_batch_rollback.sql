-- Roll back 228.08d. Existing Runtime Actions remain valid under 228.05.
SET LOCAL ROLE everydayai_owner;

REVOKE ALL ON FUNCTION submit_agent_runtime_media_image_batch_v1(
    UUID, UUID, UUID, TEXT, TEXT, UUID, TEXT, TEXT, UUID, UUID, UUID,
    TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, JSONB
) FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
       everydayai_worker, everydayai_sync, everydayai,
       everydayai_agent_runtime_worker, everydayai_projection_worker,
       everydayai_authorization_worker, everydayai_sandbox_worker,
       everydayai_runtime_admin;
DROP FUNCTION submit_agent_runtime_media_image_batch_v1(
    UUID, UUID, UUID, TEXT, TEXT, UUID, TEXT, TEXT, UUID, UUID, UUID,
    TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, JSONB
);

RESET ROLE;
