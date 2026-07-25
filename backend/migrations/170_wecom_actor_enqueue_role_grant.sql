-- 170: 恢复 OrgScopedDB 实际调用的七参数企微 Actor 原子入队能力。

SET LOCAL ROLE everydayai_owner;

REVOKE ALL ON FUNCTION enqueue_wecom_generation_turn_v2(
    JSONB, UUID, UUID, UUID, JSONB, JSONB, UUID
)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime, everydayai_worker;
GRANT EXECUTE ON FUNCTION enqueue_wecom_generation_turn_v2(
    JSONB, UUID, UUID, UUID, JSONB, JSONB, UUID
)
TO everydayai_wecom_runtime;

DO $legacy_compatibility$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'everydayai') THEN
        GRANT EXECUTE ON FUNCTION enqueue_wecom_generation_turn_v2(
            JSONB, UUID, UUID, UUID, JSONB, JSONB, UUID
        ) TO everydayai;
    END IF;
END
$legacy_compatibility$;

RESET ROLE;
