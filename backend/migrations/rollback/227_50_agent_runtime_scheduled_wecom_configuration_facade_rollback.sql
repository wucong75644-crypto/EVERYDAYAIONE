SET LOCAL ROLE everydayai_owner;

REVOKE ALL ON FUNCTION get_wecom_app_bundle()
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker, everydayai;
DROP FUNCTION get_wecom_app_bundle();

UPDATE configuration_bundle_definitions
   SET active = FALSE
 WHERE definition_version = 'v1' AND bundle_name = 'wecom.app';

UPDATE configuration_definitions
   SET contract_json =
       '{"allowed_scopes":["organization"],"bundles":["wecom.bot","wecom.contact","wecom.callback","wecom.oauth.public","wecom.oauth.exchange"],"fallback_policy":"none","key":"wecom.corp_id","secret_name":null,"user_override":"deny","validation":{"max_length":100,"min_length":1},"value_kind":"string"}'::JSONB,
       contract_hash =
       '3ab214a20f2b8e096b2b19bed390b37f050b517fd63b37817e0c8760a66b351a'
 WHERE definition_version = 'v1' AND config_key = 'wecom.corp_id';
UPDATE configuration_definitions
   SET contract_json =
       '{"allowed_scopes":["organization"],"bundles":["wecom.callback","wecom.oauth.public"],"fallback_policy":"none","key":"wecom.oauth_agent_id","secret_name":null,"user_override":"deny","validation":{"max_length":100,"min_length":1},"value_kind":"string"}'::JSONB,
       contract_hash =
       '29c6e8bec9211b29aa69b94cafabac2a0f95fd1f921eee12b8ab343cdb5f2476'
 WHERE definition_version = 'v1' AND config_key = 'wecom.oauth_agent_id';
UPDATE configuration_definitions
   SET contract_json =
       '{"allowed_scopes":["organization"],"bundles":["wecom.callback","wecom.contact","wecom.oauth.exchange"],"fallback_policy":"none","key":"wecom.oauth_agent_secret","secret_name":"wecom.oauth_agent_secret","user_override":"deny","validation":{"payload_fields":["agent_secret"],"required":["agent_secret"]},"value_kind":"secret"}'::JSONB,
       contract_hash =
       '0bcf0c906451d7f85ae319c165ab543ab0e6132e20f7b3fece2c9263ab7bf1bd'
 WHERE definition_version = 'v1' AND config_key = 'wecom.oauth_agent_secret';

RESET ROLE;
