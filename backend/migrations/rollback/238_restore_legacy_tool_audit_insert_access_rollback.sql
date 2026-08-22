-- 238 rollback: Remove only the legacy application tool-audit policy.

BEGIN;
SET LOCAL ROLE everydayai;

DROP POLICY IF EXISTS legacy_app_tool_audit_insert ON tool_audit_log;
DROP POLICY IF EXISTS legacy_app_tool_audit_all ON tool_audit_log;

RESET ROLE;
COMMIT;
