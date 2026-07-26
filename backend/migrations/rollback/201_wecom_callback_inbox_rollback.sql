SET LOCAL ROLE everydayai_owner;

DROP FUNCTION IF EXISTS cleanup_wecom_callback_inbox(INTEGER);
DROP FUNCTION IF EXISTS fail_wecom_callback(UUID, UUID, TEXT);
DROP FUNCTION IF EXISTS complete_wecom_callback(UUID, UUID);
DROP FUNCTION IF EXISTS claim_wecom_callback(INTEGER);
DROP FUNCTION IF EXISTS enqueue_wecom_callback(UUID, TEXT, TEXT, JSONB);
DROP TABLE IF EXISTS wecom_callback_inbox;
DROP FUNCTION IF EXISTS get_wecom_callback_bundle();
UPDATE configuration_bundle_definitions SET active = FALSE
 WHERE definition_version = 'v1' AND bundle_name = 'wecom.callback';
UPDATE configuration_definitions SET active = FALSE
 WHERE definition_version = 'v1'
   AND config_key = 'wecom.callback_credentials';
UPDATE configuration_definitions
   SET contract_json =
       '{"allowed_scopes":["organization"],"bundles":["wecom.bot","wecom.contact","wecom.oauth.public","wecom.oauth.exchange"],"fallback_policy":"none","key":"wecom.corp_id","secret_name":null,"user_override":"deny","validation":{"max_length":100,"min_length":1},"value_kind":"string"}'::JSONB,
       contract_hash =
       'e1a54bb65ae327fa4245fcbd34a0752ed8b5755ee6563da9d4389c44b29bd16b'
 WHERE definition_version = 'v1' AND config_key = 'wecom.corp_id';
UPDATE configuration_definitions
   SET contract_json =
       '{"allowed_scopes":["organization"],"bundles":["wecom.oauth.public"],"fallback_policy":"none","key":"wecom.oauth_agent_id","secret_name":null,"user_override":"deny","validation":{"max_length":100,"min_length":1},"value_kind":"string"}'::JSONB,
       contract_hash =
       '1c79189be97e299b2f0c27390d4ac7b8fef1dd6b1b13e0bc6f00abbac1cf6865'
 WHERE definition_version = 'v1' AND config_key = 'wecom.oauth_agent_id';
UPDATE configuration_definitions
   SET contract_json =
       '{"allowed_scopes":["organization"],"bundles":["wecom.contact","wecom.oauth.exchange"],"fallback_policy":"none","key":"wecom.oauth_agent_secret","secret_name":"wecom.oauth_agent_secret","user_override":"deny","validation":{"payload_fields":["agent_secret"],"required":["agent_secret"]},"value_kind":"secret"}'::JSONB,
       contract_hash =
       'ffc0985cd67a15d2ca3dff9a5281f41b0b05337b23cbd64c34ff31ca2bb82043'
 WHERE definition_version = 'v1' AND config_key = 'wecom.oauth_agent_secret';

RESET ROLE;
