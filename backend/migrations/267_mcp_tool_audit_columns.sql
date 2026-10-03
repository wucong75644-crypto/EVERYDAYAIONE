-- 267: Durable, queryable MCP invocation identity and replay semantics.
-- Values are allowlisted identifiers and result digests; no MCP payload/token.

SET LOCAL ROLE everydayai_owner;

ALTER TABLE tool_audit_log
    ADD COLUMN IF NOT EXISTS connector_id TEXT,
    ADD COLUMN IF NOT EXISTS capability TEXT,
    ADD COLUMN IF NOT EXISTS remote_tool_name TEXT,
    ADD COLUMN IF NOT EXISTS invocation_status TEXT,
    ADD COLUMN IF NOT EXISTS replay_requirement TEXT,
    ADD COLUMN IF NOT EXISTS replayed BOOLEAN,
    ADD COLUMN IF NOT EXISTS result_sha256 TEXT,
    ADD COLUMN IF NOT EXISTS error_code TEXT;

ALTER TABLE tool_audit_log
    ADD CONSTRAINT tool_audit_mcp_connector_allowlist CHECK (
        connector_id IS NULL OR connector_id = 'test-readonly'
    ),
    ADD CONSTRAINT tool_audit_mcp_digest_format CHECK (
        result_sha256 IS NULL OR result_sha256 ~ '^[0-9a-f]{64}$'
    );

CREATE INDEX IF NOT EXISTS idx_tool_audit_mcp_connector
    ON tool_audit_log (org_id, connector_id, created_at DESC)
    WHERE connector_id IS NOT NULL;
