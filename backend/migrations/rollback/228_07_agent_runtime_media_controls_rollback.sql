-- Roll back 228.07 only before Runtime media control facts exist.
SET LOCAL ROLE everydayai_owner;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM agent_runtime_media_cancel_requests)
       OR EXISTS (SELECT 1 FROM agent_runtime_media_retry_lineage) THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_MESSAGE_CONTROL_IN_USE'
            USING ERRCODE = '55000';
    END IF;
END $$;

DROP FUNCTION read_agent_runtime_media_retry_binding_v1(UUID,UUID,TEXT,UUID,BIGINT,TEXT);
DROP FUNCTION retry_agent_runtime_media_slot_v1(
    UUID,UUID,INTEGER,UUID,BIGINT,UUID,UUID,TEXT,TEXT,TEXT
);
DROP FUNCTION request_agent_runtime_media_message_cancel_v1(UUID,UUID,UUID,TEXT);
DROP FUNCTION _agent_runtime_media_web_control_v1(UUID,UUID);
DROP TABLE agent_runtime_media_retry_lineage;
DROP TABLE agent_runtime_media_cancel_requests;

RESET ROLE;
