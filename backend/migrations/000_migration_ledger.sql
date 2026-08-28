-- Migration ledger bootstrap.
-- Identity is the complete migration filename; numeric prefixes are ordering hints only.

CREATE TABLE IF NOT EXISTS schema_migration_ledger (
    identity TEXT PRIMARY KEY,
    checksum_sha256 TEXT NOT NULL CHECK (checksum_sha256 ~ '^[0-9a-f]{64}$'),
    status TEXT NOT NULL CHECK (status IN ('applied', 'failed')),
    execution_kind TEXT NOT NULL CHECK (execution_kind IN ('migration', 'baseline')),
    rollback_identity TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    applied_by TEXT NOT NULL,
    error_summary TEXT,
    CONSTRAINT schema_migration_finished_state_check CHECK (
        (status = 'applied' AND finished_at IS NOT NULL AND error_summary IS NULL)
        OR status = 'failed'
    )
);

REVOKE ALL ON TABLE schema_migration_ledger FROM PUBLIC;

COMMENT ON TABLE schema_migration_ledger IS
    'Authoritative migration identity/checksum ledger. Full filenames are immutable identities.';
