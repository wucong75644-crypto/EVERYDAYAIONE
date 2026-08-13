SET LOCAL ROLE everydayai_owner;
REVOKE ALL ON FUNCTION submit_agent_runtime_media_action_v1(
    UUID, UUID, UUID, TEXT, TEXT, UUID, TEXT, TEXT, UUID, UUID, UUID, UUID,
    TEXT, JSONB, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT
) FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker, everydayai_sync, everydayai;
DROP FUNCTION submit_agent_runtime_media_action_v1(
    UUID, UUID, UUID, TEXT, TEXT, UUID, TEXT, TEXT, UUID, UUID, UUID, UUID,
    TEXT, JSONB, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT
);
RESET ROLE;
