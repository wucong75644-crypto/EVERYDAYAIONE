SET LOCAL ROLE everydayai_owner;

DROP FUNCTION IF EXISTS list_user_configuration_status();
DROP FUNCTION IF EXISTS list_org_configuration_status(UUID);
DROP FUNCTION IF EXISTS list_platform_configuration_status();
DROP FUNCTION IF EXISTS delete_user_configuration(TEXT, BIGINT);
DROP FUNCTION IF EXISTS delete_org_configuration(UUID, TEXT, BIGINT);
DROP FUNCTION IF EXISTS delete_platform_configuration(TEXT, BIGINT);
DROP FUNCTION IF EXISTS set_user_configuration(
    UUID, TEXT, TEXT, JSONB, JSONB, BIGINT
);
DROP FUNCTION IF EXISTS set_org_configuration(
    UUID, TEXT, TEXT, JSONB, JSONB, BIGINT
);
DROP FUNCTION IF EXISTS set_platform_configuration(
    TEXT, TEXT, JSONB, JSONB, BIGINT
);

RESET ROLE;
