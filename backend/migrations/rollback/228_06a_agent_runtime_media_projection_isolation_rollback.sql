SET LOCAL ROLE everydayai_owner;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM agent_runtime_media_projection_isolations) THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_ISOLATION_AUDIT_PRESENT'
            USING ERRCODE='55000';
    END IF;
END $$;
DROP FUNCTION isolate_dead_agent_runtime_media_projection_v1(UUID,BIGINT,INTEGER,UUID,TEXT);
DROP FUNCTION isolate_agent_runtime_media_projection_v1(UUID,UUID,TEXT);
DROP FUNCTION _agent_runtime_media_isolate_terminal_v1(UUID,TEXT,UUID,UUID,TEXT,UUID,BIGINT,INTEGER,TEXT,TEXT);
DROP TABLE agent_runtime_media_projection_isolations;
RESET ROLE;
