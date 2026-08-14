SET LOCAL ROLE everydayai_owner;
REVOKE ALL ON FUNCTION submit_agent_runtime_chat_action_v1(
    UUID, UUID, UUID, TEXT, TEXT, TEXT, INTEGER, TEXT, JSONB, TEXT, TEXT,
    TEXT, TEXT, TEXT, TEXT, INTEGER, JSONB, JSONB, TEXT
) FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker, everydayai_sync, everydayai;
DROP FUNCTION submit_agent_runtime_chat_action_v1(
    UUID, UUID, UUID, TEXT, TEXT, TEXT, INTEGER, TEXT, JSONB, TEXT, TEXT,
    TEXT, TEXT, TEXT, TEXT, INTEGER, JSONB, JSONB, TEXT
);
RESET ROLE;
