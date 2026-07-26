SET LOCAL ROLE everydayai_owner;

GRANT SELECT, UPDATE, DELETE ON error_logs TO everydayai_runtime;
ALTER TABLE error_logs NO FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS error_logs_owner_all ON error_logs;
CREATE POLICY platform_admin_error_logs_select ON error_logs
FOR SELECT TO everydayai_runtime USING (tenant_platform_admin());
CREATE POLICY platform_admin_error_logs_update ON error_logs
FOR UPDATE TO everydayai_runtime
USING (tenant_platform_admin()) WITH CHECK (tenant_platform_admin());
CREATE POLICY platform_admin_error_logs_delete ON error_logs
FOR DELETE TO everydayai_runtime USING (tenant_platform_admin());

ALTER TABLE permission_audit_log NO FORCE ROW LEVEL SECURITY;
ALTER TABLE permission_audit_log DISABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS permission_audit_log_owner_all ON permission_audit_log;

DROP FUNCTION IF EXISTS clear_platform_errors(DATE, BOOLEAN);
DROP FUNCTION IF EXISTS resolve_platform_error(BIGINT);
DROP FUNCTION IF EXISTS list_platform_error_summary(INTEGER);
DROP FUNCTION IF EXISTS get_platform_error_stats();
DROP FUNCTION IF EXISTS list_platform_error_logs(
    INTEGER, INTEGER, TEXT, BOOLEAN, BOOLEAN, TEXT, INTEGER
);

RESET ROLE;
