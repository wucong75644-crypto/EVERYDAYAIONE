SET LOCAL ROLE everydayai_owner;

REVOKE ALL ON FUNCTION
    get_model_attempt(UUID),
    claim_model_attempt_reconciliation(UUID, UUID, BIGINT, TEXT, INTEGER),
    renew_model_attempt_reconciliation(UUID, UUID, UUID, INTEGER),
    resolve_model_attempt(
        UUID, UUID, UUID, BIGINT, BIGINT, TEXT, TEXT, JSONB, TEXT,
        TEXT, TEXT, JSONB, INTEGER, TEXT, JSONB
    ),
    _adjust_model_attempt_credits(UUID, TEXT, INTEGER),
    record_late_model_receipt(UUID, TEXT, JSONB, TEXT, JSONB, TEXT, JSONB, INTEGER)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync, everydayai;

DROP FUNCTION IF EXISTS
    record_late_model_receipt(UUID, TEXT, JSONB, TEXT, JSONB, TEXT, JSONB, INTEGER),
    _adjust_model_attempt_credits(UUID, TEXT, INTEGER),
    resolve_model_attempt(
        UUID, UUID, UUID, BIGINT, BIGINT, TEXT, TEXT, JSONB, TEXT,
        TEXT, TEXT, JSONB, INTEGER, TEXT, JSONB
    ),
    renew_model_attempt_reconciliation(UUID, UUID, UUID, INTEGER),
    claim_model_attempt_reconciliation(UUID, UUID, BIGINT, TEXT, INTEGER),
    get_model_attempt(UUID);

RESET ROLE;
