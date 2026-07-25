SET LOCAL ROLE everydayai_owner;

REVOKE ALL ON FUNCTION
    claim_conversation_delivery(INTEGER, INTEGER),
    renew_conversation_delivery(UUID, UUID, INTEGER, JSONB),
    complete_conversation_delivery(UUID, UUID, JSONB),
    fail_conversation_delivery(UUID, UUID, TEXT, JSONB, INTEGER),
    worker_get_conversation_delivery_payload(UUID, UUID)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker, everydayai;

DROP FUNCTION worker_get_conversation_delivery_payload(UUID, UUID);
DROP FUNCTION claim_conversation_delivery(INTEGER, INTEGER);
DROP FUNCTION renew_conversation_delivery(UUID, UUID, INTEGER, JSONB);
DROP FUNCTION complete_conversation_delivery(UUID, UUID, JSONB);
DROP FUNCTION fail_conversation_delivery(UUID, UUID, TEXT, JSONB, INTEGER);
DROP FUNCTION _assert_wecom_delivery_worker_scope();

ALTER FUNCTION _claim_conversation_delivery_core(INTEGER, INTEGER)
    RENAME TO claim_conversation_delivery;
ALTER FUNCTION _renew_conversation_delivery_core(UUID, UUID, INTEGER, JSONB)
    RENAME TO renew_conversation_delivery;
ALTER FUNCTION _complete_conversation_delivery_core(UUID, UUID, JSONB)
    RENAME TO complete_conversation_delivery;
ALTER FUNCTION _fail_conversation_delivery_core(
    UUID, UUID, TEXT, JSONB, INTEGER
) RENAME TO fail_conversation_delivery;

DO $legacy_compatibility$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'everydayai') THEN
        GRANT EXECUTE ON FUNCTION
            claim_conversation_delivery(INTEGER, INTEGER),
            renew_conversation_delivery(UUID, UUID, INTEGER, JSONB),
            complete_conversation_delivery(UUID, UUID, JSONB),
            fail_conversation_delivery(UUID, UUID, TEXT, JSONB, INTEGER)
        TO everydayai;
    END IF;
END
$legacy_compatibility$;

RESET ROLE;
