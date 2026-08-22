-- 238: Restore only the legacy application's tool-audit access.
-- Memory tables and Agent-Runtime tables are intentionally untouched.

BEGIN;
SET LOCAL ROLE everydayai;

ALTER TABLE tool_audit_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE tool_audit_log FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS legacy_app_tool_audit_insert ON tool_audit_log;
DROP POLICY IF EXISTS legacy_app_tool_audit_all ON tool_audit_log;
CREATE POLICY legacy_app_tool_audit_all
ON tool_audit_log
FOR ALL TO everydayai
USING (FALSE)
WITH CHECK (TRUE);

RESET ROLE;
COMMIT;
