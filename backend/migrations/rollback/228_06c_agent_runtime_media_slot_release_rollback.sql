SET LOCAL ROLE everydayai_owner;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM agent_runtime_media_slot_release_outbox)
       OR EXISTS (SELECT 1 FROM agent_runtime_media_slot_release_recoveries) THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_SLOT_RELEASE_HISTORY_PRESENT'
            USING ERRCODE='55000';
    END IF;
END $$;
DROP TRIGGER agent_runtime_media_slot_release_enqueue_v1
    ON agent_runtime_media_projection_results;
DROP FUNCTION _enqueue_agent_runtime_media_slot_release_v1();
DROP FUNCTION requeue_agent_runtime_media_slot_release_v1(
    UUID,BIGINT,INTEGER,UUID,TEXT,TIMESTAMPTZ
);
DROP FUNCTION fail_agent_runtime_media_slot_release_v1(UUID,UUID,TEXT);
DROP FUNCTION ack_agent_runtime_media_slot_release_v1(UUID,UUID);
DROP FUNCTION claim_agent_runtime_media_slot_release_v1(INTEGER,INTEGER);
DROP TABLE agent_runtime_media_slot_release_recoveries;
DROP TABLE agent_runtime_media_slot_release_outbox;
RESET ROLE;
