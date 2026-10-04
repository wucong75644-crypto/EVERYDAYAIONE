DROP INDEX IF EXISTS idx_tool_audit_mcp_connector;
ALTER TABLE tool_audit_log
    DROP CONSTRAINT IF EXISTS tool_audit_mcp_digest_format,
    DROP CONSTRAINT IF EXISTS tool_audit_mcp_connector_allowlist,
    DROP COLUMN IF EXISTS error_code,
    DROP COLUMN IF EXISTS result_sha256,
    DROP COLUMN IF EXISTS replayed,
    DROP COLUMN IF EXISTS replay_requirement,
    DROP COLUMN IF EXISTS invocation_status,
    DROP COLUMN IF EXISTS remote_tool_name,
    DROP COLUMN IF EXISTS capability,
    DROP COLUMN IF EXISTS connector_id;
