SET LOCAL ROLE everydayai_owner;

REVOKE ALL ON FUNCTION get_wecom_bot_admin_test_bundle()
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync, everydayai;

DROP FUNCTION IF EXISTS get_wecom_bot_admin_test_bundle();

RESET ROLE;
