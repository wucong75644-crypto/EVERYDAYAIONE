/* Roll back 228.08g2 only before a terminal WeCom outbox was derived. */
SET LOCAL ROLE everydayai_owner;

DO $guard$
BEGIN
    IF EXISTS(SELECT 1 FROM agent_runtime_media_wecom_outbox_facts_v1) THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_228_08G2_WECOM_OUTBOX_IN_USE'
            USING ERRCODE='55000';
    END IF;
END
$guard$;

DROP TRIGGER agent_runtime_media_model_video_wecom_outbox_v1
    ON agent_projection_outbox;
DROP FUNCTION _derive_agent_runtime_model_video_wecom_outbox_v1();
DROP TABLE agent_runtime_media_wecom_outbox_facts_v1;

RESET ROLE;
