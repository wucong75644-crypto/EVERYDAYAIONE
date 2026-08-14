-- Roll back 228.08d. Existing Runtime Actions remain valid under 228.05.
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
