SET LOCAL ROLE everydayai_owner;

REVOKE EXECUTE ON FUNCTION record_runtime_tool_audit(
    UUID, TEXT, TEXT, INTEGER, TEXT, INTEGER, INTEGER, TEXT,
    BOOLEAN, BOOLEAN, INTEGER, INTEGER, TEXT
) FROM everydayai_runtime;
DROP FUNCTION record_runtime_tool_audit(
    UUID, TEXT, TEXT, INTEGER, TEXT, INTEGER, INTEGER, TEXT,
    BOOLEAN, BOOLEAN, INTEGER, INTEGER, TEXT
);

ALTER FUNCTION maintain_tool_audit_partitions() SECURITY INVOKER;
GRANT EXECUTE ON FUNCTION maintain_tool_audit_partitions() TO everydayai;

RESET ROLE;
