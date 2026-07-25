REVOKE EXECUTE ON FUNCTION discover_wecom_bot_targets()
FROM everydayai_worker;

DROP FUNCTION IF EXISTS discover_wecom_bot_targets();
DROP FUNCTION IF EXISTS _assert_wecom_worker_discovery_scope();
