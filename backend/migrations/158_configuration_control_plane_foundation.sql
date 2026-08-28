-- 158: Unified configuration registry projection and protected fact tables.
-- Prerequisites: migrations 156-157 and second-wave ownership transfer.

SET LOCAL ROLE everydayai_owner;

CREATE TABLE configuration_definitions (
    definition_version VARCHAR(32) NOT NULL,
    config_key VARCHAR(120) NOT NULL,
    contract_json JSONB NOT NULL,
    contract_hash VARCHAR(64) NOT NULL,
    active BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (definition_version, config_key),
    CONSTRAINT configuration_definition_key_match CHECK (
        contract_json->>'key' = config_key
    ),
    CONSTRAINT configuration_definition_contract_object CHECK (
        jsonb_typeof(contract_json) = 'object'
    ),
    CONSTRAINT configuration_definition_hash_format CHECK (
        contract_hash ~ '^[0-9a-f]{64}$'
    )
);

CREATE UNIQUE INDEX uq_configuration_definition_active_key
    ON configuration_definitions(config_key)
    WHERE active;

COMMENT ON TABLE configuration_definitions IS
    'Migration-pinned projection of the canonical code configuration registry';
COMMENT ON COLUMN configuration_definitions.contract_hash IS
    'SHA-256 of canonical JSON; application startup rejects registry drift';

INSERT INTO configuration_definitions(
    definition_version, config_key, contract_json, contract_hash, active
) VALUES
    (
        'v1', 'ai.dashscope.api_key',
        '{"allowed_scopes":["platform","organization","user"],"bundles":["ai.provider.dashscope"],"fallback_policy":"org_then_platform","key":"ai.dashscope.api_key","secret_name":"ai.dashscope_api_key","user_override":"org_policy","validation":{"payload_fields":["api_key"],"required":["api_key"]},"value_kind":"secret"}'::JSONB,
        '8be8cef645ef1437fe8e628a3ee8eb7f20505e43de41c605816205e2c268e173',
        TRUE
    ),
    (
        'v1', 'ai.openrouter.api_key',
        '{"allowed_scopes":["platform","organization","user"],"bundles":["ai.provider.openrouter"],"fallback_policy":"org_then_platform","key":"ai.openrouter.api_key","secret_name":"ai.openrouter_api_key","user_override":"org_policy","validation":{"payload_fields":["api_key"],"required":["api_key"]},"value_kind":"secret"}'::JSONB,
        '9b3c24d408995a7d7d103ea2ab6e9989796b1a98c580f9d25aeeb004c908741f',
        TRUE
    ),
    (
        'v1', 'ai.kie.api_key',
        '{"allowed_scopes":["platform","organization","user"],"bundles":["ai.provider.kie"],"fallback_policy":"org_then_platform","key":"ai.kie.api_key","secret_name":"ai.kie_api_key","user_override":"org_policy","validation":{"payload_fields":["api_key"],"required":["api_key"]},"value_kind":"secret"}'::JSONB,
        '3076c3eb271152db253eb3bb3a6167cc22752730a5a925c65d418e09bd4b39b0',
        TRUE
    ),
    (
        'v1', 'ai.google.api_key',
        '{"allowed_scopes":["platform","organization","user"],"bundles":["ai.provider.google"],"fallback_policy":"org_then_platform","key":"ai.google.api_key","secret_name":"ai.google_api_key","user_override":"org_policy","validation":{"payload_fields":["api_key"],"required":["api_key"]},"value_kind":"secret"}'::JSONB,
        '6a3ed7a9e4cafa958f04355f992a88265d466013093eda0deae287d1fe160af4',
        TRUE
    ),
    (
        'v1', 'erp.app_credentials',
        '{"allowed_scopes":["organization"],"bundles":["erp.runtime"],"fallback_policy":"none","key":"erp.app_credentials","secret_name":"erp.app_credentials","user_override":"deny","validation":{"payload_fields":["app_key","app_secret"],"required":["app_key","app_secret"]},"value_kind":"secret"}'::JSONB,
        '81086fd52de82c63ec161f57303a36ff1d8d9357e65e02b34eaa65b5908181e8',
        TRUE
    ),
    (
        'v1', 'erp.token_pair',
        '{"allowed_scopes":["organization"],"bundles":["erp.runtime"],"fallback_policy":"none","key":"erp.token_pair","secret_name":"erp.token_pair","user_override":"deny","validation":{"payload_fields":["access_token","refresh_token"],"required":["access_token","refresh_token"]},"value_kind":"secret"}'::JSONB,
        '61e2378e13b21300072f13c20844e409757f3d79879088897c1a8f4c594e7488',
        TRUE
    ),
    (
        'v1', 'erp.warehouse_ids',
        '{"allowed_scopes":["organization"],"bundles":["erp.runtime"],"fallback_policy":"none","key":"erp.warehouse_ids","secret_name":null,"user_override":"deny","validation":{"item_type":"string","type":"array","unique":true},"value_kind":"json"}'::JSONB,
        '70f31f9fc53d77378512c9d210987741b6b6d08dc98765660704785bea07f83c',
        TRUE
    ),
    (
        'v1', 'wecom.corp_id',
        '{"allowed_scopes":["organization"],"bundles":["wecom.bot","wecom.contact","wecom.oauth.public","wecom.oauth.exchange"],"fallback_policy":"none","key":"wecom.corp_id","secret_name":null,"user_override":"deny","validation":{"max_length":100,"min_length":1},"value_kind":"string"}'::JSONB,
        'e1a54bb65ae327fa4245fcbd34a0752ed8b5755ee6563da9d4389c44b29bd16b',
        TRUE
    ),
    (
        'v1', 'wecom.bot_credentials',
        '{"allowed_scopes":["organization"],"bundles":["wecom.bot"],"fallback_policy":"none","key":"wecom.bot_credentials","secret_name":"wecom.bot_credentials","user_override":"deny","validation":{"payload_fields":["bot_id","bot_secret"],"required":["bot_id","bot_secret"]},"value_kind":"secret"}'::JSONB,
        '88da03493f9b3b272d256733ad638c5b39c8696149791c6c668fff843f5f6e61',
        TRUE
    ),
    (
        'v1', 'wecom.oauth_agent_id',
        '{"allowed_scopes":["organization"],"bundles":["wecom.oauth.public"],"fallback_policy":"none","key":"wecom.oauth_agent_id","secret_name":null,"user_override":"deny","validation":{"max_length":100,"min_length":1},"value_kind":"string"}'::JSONB,
        '1c79189be97e299b2f0c27390d4ac7b8fef1dd6b1b13e0bc6f00abbac1cf6865',
        TRUE
    ),
    (
        'v1', 'wecom.oauth_agent_secret',
        '{"allowed_scopes":["organization"],"bundles":["wecom.contact","wecom.oauth.exchange"],"fallback_policy":"none","key":"wecom.oauth_agent_secret","secret_name":"wecom.oauth_agent_secret","user_override":"deny","validation":{"payload_fields":["agent_secret"],"required":["agent_secret"]},"value_kind":"secret"}'::JSONB,
        'ffc0985cd67a15d2ca3dff9a5281f41b0b05337b23cbd64c34ff31ca2bb82043',
        TRUE
    ),
    (
        'v1', 'kuaimai_external.thinktank.cookie',
        '{"allowed_scopes":["organization"],"bundles":["kuaimai_external.thinktank"],"fallback_policy":"none","key":"kuaimai_external.thinktank.cookie","secret_name":"kuaimai_external.thinktank_cookie","user_override":"deny","validation":{"payload_fields":["censeid_cookie","cookie_full"],"required":["censeid_cookie","cookie_full"]},"value_kind":"secret"}'::JSONB,
        'b973e37b9bde965d5125899d62be4815b187f2dd880b8d9b3686e5d1385752c6',
        TRUE
    ),
    (
        'v1', 'kuaimai_external.thinktank.company_id',
        '{"allowed_scopes":["organization"],"bundles":["kuaimai_external.thinktank"],"fallback_policy":"none","key":"kuaimai_external.thinktank.company_id","secret_name":null,"user_override":"deny","validation":{"max_length":100,"min_length":1},"value_kind":"string"}'::JSONB,
        'a0acb1ae40dd6ae57a77b4a27141cd757414bbf23f6467878b0e11878b6e6a92',
        TRUE
    ),
    (
        'v1', 'kuaimai_external.viperp.cookie',
        '{"allowed_scopes":["organization"],"bundles":["kuaimai_external.viperp"],"fallback_policy":"none","key":"kuaimai_external.viperp.cookie","secret_name":"kuaimai_external.viperp_cookie","user_override":"deny","validation":{"payload_fields":["censeid_cookie","cookie_full"],"required":["censeid_cookie","cookie_full"]},"value_kind":"secret"}'::JSONB,
        'b74b8708af25ad41a660ad38b140136c64609d85193bd1755176245e28368455',
        TRUE
    ),
    (
        'v1', 'kuaimai_external.viperp.company_id',
        '{"allowed_scopes":["organization"],"bundles":["kuaimai_external.viperp"],"fallback_policy":"none","key":"kuaimai_external.viperp.company_id","secret_name":null,"user_override":"deny","validation":{"max_length":100,"min_length":1},"value_kind":"string"}'::JSONB,
        '004454ac2f29ca6a8f6a42c196bed6b17bff1d443fb9ed40f45d57530761a019',
        TRUE
    );

CREATE TABLE secret_records (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_kind VARCHAR(20) NOT NULL
        CHECK (scope_kind IN ('platform', 'organization', 'user')),
    org_id UUID REFERENCES organizations(id) ON DELETE RESTRICT,
    user_id UUID REFERENCES users(id) ON DELETE RESTRICT,
    secret_name VARCHAR(120) NOT NULL CHECK (BTRIM(secret_name) <> ''),
    payload_ciphertext TEXT NOT NULL CHECK (BTRIM(payload_ciphertext) <> ''),
    wrapped_dek TEXT NOT NULL CHECK (BTRIM(wrapped_dek) <> ''),
    kek_version VARCHAR(64) NOT NULL CHECK (BTRIM(kek_version) <> ''),
    payload_version BIGINT NOT NULL DEFAULT 1 CHECK (payload_version > 0),
    status VARCHAR(20) NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'retired', 'revoked')),
    expires_at TIMESTAMPTZ,
    rotated_from UUID REFERENCES secret_records(id) ON DELETE RESTRICT,
    created_by UUID REFERENCES users(id) ON DELETE RESTRICT,
    updated_by UUID REFERENCES users(id) ON DELETE RESTRICT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT secret_record_scope_check CHECK (
        (scope_kind = 'platform' AND org_id IS NULL AND user_id IS NULL)
        OR (scope_kind = 'organization' AND org_id IS NOT NULL AND user_id IS NULL)
        OR (scope_kind = 'user' AND org_id IS NULL AND user_id IS NOT NULL)
    )
);

CREATE UNIQUE INDEX uq_secret_record_active_scope_name
    ON secret_records(scope_kind, org_id, user_id, secret_name)
    NULLS NOT DISTINCT
    WHERE status = 'active';
CREATE INDEX idx_secret_record_org ON secret_records(org_id)
    WHERE org_id IS NOT NULL;
CREATE INDEX idx_secret_record_user ON secret_records(user_id)
    WHERE user_id IS NOT NULL;

COMMENT ON TABLE secret_records IS
    'Envelope-encrypted secret payloads; plaintext and unwrapped DEKs are forbidden';

CREATE TABLE configuration_entries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_kind VARCHAR(20) NOT NULL
        CHECK (scope_kind IN ('platform', 'organization', 'user')),
    org_id UUID REFERENCES organizations(id) ON DELETE RESTRICT,
    user_id UUID REFERENCES users(id) ON DELETE RESTRICT,
    definition_version VARCHAR(32) NOT NULL,
    config_key VARCHAR(120) NOT NULL,
    value_json JSONB,
    secret_id UUID REFERENCES secret_records(id) ON DELETE RESTRICT,
    status VARCHAR(20) NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'disabled')),
    version BIGINT NOT NULL DEFAULT 1 CHECK (version > 0),
    updated_by UUID REFERENCES users(id) ON DELETE RESTRICT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT configuration_entry_definition_fk
        FOREIGN KEY (definition_version, config_key)
        REFERENCES configuration_definitions(definition_version, config_key)
        ON DELETE RESTRICT,
    CONSTRAINT configuration_entry_scope_check CHECK (
        (scope_kind = 'platform' AND org_id IS NULL AND user_id IS NULL)
        OR (scope_kind = 'organization' AND org_id IS NOT NULL AND user_id IS NULL)
        OR (scope_kind = 'user' AND org_id IS NULL AND user_id IS NOT NULL)
    ),
    CONSTRAINT configuration_entry_value_check CHECK (
        (value_json IS NULL) <> (secret_id IS NULL)
    ),
    UNIQUE NULLS NOT DISTINCT (scope_kind, org_id, user_id, config_key)
);

CREATE INDEX idx_configuration_entry_org ON configuration_entries(org_id)
    WHERE org_id IS NOT NULL;
CREATE INDEX idx_configuration_entry_user ON configuration_entries(user_id)
    WHERE user_id IS NOT NULL;
CREATE INDEX idx_configuration_entry_secret ON configuration_entries(secret_id)
    WHERE secret_id IS NOT NULL;

COMMENT ON TABLE configuration_entries IS
    'Scoped ordinary configuration values or references to encrypted secret records';

CREATE TABLE configuration_policies (
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
    definition_version VARCHAR(32) NOT NULL,
    config_key VARCHAR(120) NOT NULL,
    allow_user_override BOOLEAN NOT NULL DEFAULT FALSE,
    locked BOOLEAN NOT NULL DEFAULT FALSE,
    version BIGINT NOT NULL DEFAULT 1 CHECK (version > 0),
    updated_by UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (org_id, config_key),
    CONSTRAINT configuration_policy_definition_fk
        FOREIGN KEY (definition_version, config_key)
        REFERENCES configuration_definitions(definition_version, config_key)
        ON DELETE RESTRICT
);

COMMENT ON TABLE configuration_policies IS
    'Organization controls for personal configuration overrides';

ALTER TABLE secret_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE secret_records FORCE ROW LEVEL SECURITY;
ALTER TABLE configuration_entries ENABLE ROW LEVEL SECURITY;
ALTER TABLE configuration_entries FORCE ROW LEVEL SECURITY;
ALTER TABLE configuration_policies ENABLE ROW LEVEL SECURITY;
ALTER TABLE configuration_policies FORCE ROW LEVEL SECURITY;

CREATE POLICY secret_records_owner_only ON secret_records
TO everydayai_owner
USING (current_user = 'everydayai_owner')
WITH CHECK (current_user = 'everydayai_owner');
CREATE POLICY configuration_entries_owner_only ON configuration_entries
TO everydayai_owner
USING (current_user = 'everydayai_owner')
WITH CHECK (current_user = 'everydayai_owner');
CREATE POLICY configuration_policies_owner_only ON configuration_policies
TO everydayai_owner
USING (current_user = 'everydayai_owner')
WITH CHECK (current_user = 'everydayai_owner');

REVOKE ALL ON TABLE configuration_definitions,
    secret_records,
    configuration_entries,
    configuration_policies
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime, everydayai_worker;

RESET ROLE;
