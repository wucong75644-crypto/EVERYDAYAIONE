-- Intentional no-op: pgcrypto is a shared database-platform capability.
-- Its lifecycle belongs to the DBA/platform, not to migration 227_01_z.
SELECT 'no-op: pgcrypto remains managed by the database platform' AS rollback_policy;
