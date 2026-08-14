SET LOCAL ROLE everydayai_owner;

DO $guard$
BEGIN
    IF EXISTS (SELECT 1 FROM agent_projection_dead_recoveries) THEN
        RAISE EXCEPTION 'AGENT_PROJECTION_DEAD_RECOVERY_ROLLBACK_HAS_FACTS'
            USING ERRCODE = '55000';
    END IF;
END
$guard$;

DROP FUNCTION requeue_agent_projection_dead(
    UUID, TEXT, BIGINT, INTEGER, UUID, TEXT, TIMESTAMPTZ);
DROP FUNCTION get_agent_projection_dead_item(UUID);
DROP FUNCTION list_agent_projection_dead_items(INTEGER);
DROP FUNCTION apply_agent_compat_projection(UUID, UUID, TEXT);
DROP FUNCTION claim_agent_projection_outbox(INTEGER, INTEGER);

ALTER FUNCTION _apply_agent_compat_projection_220_12(UUID, UUID, TEXT)
    RENAME TO apply_agent_compat_projection;
ALTER FUNCTION _claim_agent_projection_outbox_215(INTEGER, INTEGER)
    RENAME TO claim_agent_projection_outbox;
GRANT EXECUTE ON FUNCTION
    claim_agent_projection_outbox(INTEGER, INTEGER),
    apply_agent_compat_projection(UUID, UUID, TEXT)
TO everydayai_worker;

DROP TABLE agent_projection_dead_recoveries;
ALTER TABLE agent_projection_outbox
    DROP COLUMN recovery_count,
    DROP COLUMN recovery_version;

RESET ROLE;
