SET LOCAL ROLE everydayai_owner;

DROP FUNCTION IF EXISTS get_web_wecom_binding_status(UUID);
DROP FUNCTION IF EXISTS unbind_web_wecom_identity(UUID);
DROP FUNCTION IF EXISTS bind_web_wecom_identity(
    TEXT, TEXT, UUID, TEXT, TEXT, TIMESTAMPTZ
);
DROP FUNCTION IF EXISTS commit_web_wecom_login(
    TEXT, TEXT, UUID, TEXT, TEXT, TIMESTAMPTZ
);
DROP FUNCTION IF EXISTS get_web_wecom_oauth_exchange_config(UUID);
DROP FUNCTION IF EXISTS get_web_wecom_oauth_public_config(UUID);
DROP FUNCTION IF EXISTS _assert_web_wecom_oauth_scope(UUID, BOOLEAN);

RESET ROLE;
