SET LOCAL ROLE everydayai_owner;

REVOKE EXECUTE ON FUNCTION worker_model_scoring_snapshot(UUID),
    worker_commit_model_score(
        UUID, UUID, TEXT, TEXT, DOUBLE PRECISION, DOUBLE PRECISION,
        DOUBLE PRECISION, INTEGER, JSONB, TIMESTAMPTZ, TIMESTAMPTZ,
        TEXT, TEXT, TEXT, JSONB, DOUBLE PRECISION, TEXT, TEXT
    )
FROM everydayai_worker;
DROP FUNCTION worker_model_scoring_snapshot(UUID);
DROP FUNCTION worker_commit_model_score(
    UUID, UUID, TEXT, TEXT, DOUBLE PRECISION, DOUBLE PRECISION,
    DOUBLE PRECISION, INTEGER, JSONB, TIMESTAMPTZ, TIMESTAMPTZ,
    TEXT, TEXT, TEXT, JSONB, DOUBLE PRECISION, TEXT, TEXT
);
DROP FUNCTION _assert_worker_model_scoring_scope(UUID);

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM scoring_audit_log WHERE owner_user_id IS NOT NULL
    ) THEN
        RAISE EXCEPTION 'SCORING_PERSONAL_FACTS_REQUIRE_FORWARD_ROLLBACK';
    END IF;
END;
$$;

DROP INDEX idx_scoring_audit_owner_user;
ALTER TABLE scoring_audit_log
    DROP CONSTRAINT scoring_audit_owner_scope_check;
ALTER TABLE scoring_audit_log DROP COLUMN owner_user_id;

RESET ROLE;
