-- Roll back fixed 160 Bundle facades before the resolution core.

DROP FUNCTION IF EXISTS get_kuaimai_viperp_bundle();
DROP FUNCTION IF EXISTS get_kuaimai_thinktank_bundle();
DROP FUNCTION IF EXISTS get_wecom_contact_bundle();
DROP FUNCTION IF EXISTS get_wecom_oauth_exchange_bundle();
DROP FUNCTION IF EXISTS get_wecom_oauth_public_bundle();
DROP FUNCTION IF EXISTS get_wecom_bot_bundle();
DROP FUNCTION IF EXISTS get_erp_runtime_bundle();
DROP FUNCTION IF EXISTS get_ai_google_bundle();
DROP FUNCTION IF EXISTS get_ai_kie_bundle();
DROP FUNCTION IF EXISTS get_ai_openrouter_bundle();
DROP FUNCTION IF EXISTS get_ai_dashscope_bundle();
DROP FUNCTION IF EXISTS _assert_configuration_runtime_org_admin();
DROP FUNCTION IF EXISTS _assert_configuration_wecom_actor();
DROP FUNCTION IF EXISTS _assert_configuration_worker_org();
DROP FUNCTION IF EXISTS _assert_configuration_runtime_oauth();
DROP FUNCTION IF EXISTS _assert_configuration_runtime_actor(BOOLEAN);
