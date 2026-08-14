-- AR-17.4 additive tenant-scoped provider readiness lane.
-- 227.01/227.02 remain immutable.
SET LOCAL ROLE everydayai_owner;

CREATE TABLE agent_runtime_tenant_provider_bindings (
    catalog_revision TEXT NOT NULL REFERENCES agent_runtime_catalog_facts(catalog_revision),
    tool_name TEXT NOT NULL,
    scope_kind TEXT NOT NULL CHECK (scope_kind IN ('user','channel')),
    scope_id TEXT NOT NULL CHECK (length(btrim(scope_id)) BETWEEN 1 AND 200),
    org_id UUID,
    provider_revision TEXT NOT NULL CHECK (length(btrim(provider_revision)) BETWEEN 1 AND 200),
    credential_handle TEXT CHECK (credential_handle IS NULL OR length(btrim(credential_handle)) BETWEEN 1 AND 200),
    readiness_hash TEXT NOT NULL CHECK (readiness_hash ~ '^[0-9a-f]{64}$'),
    CHECK (scope_kind='user' OR org_id IS NOT NULL),
    service_wiring_ready BOOLEAN NOT NULL DEFAULT FALSE,
    credential_available BOOLEAN NOT NULL DEFAULT FALSE,
    capability_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    probe_passed BOOLEAN NOT NULL DEFAULT FALSE,
    ready BOOLEAN GENERATED ALWAYS AS (
        service_wiring_ready AND credential_available
        AND capability_enabled AND probe_passed
    ) STORED,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY(catalog_revision,tool_name,scope_kind,scope_id)
);
ALTER TABLE agent_runtime_tenant_provider_bindings ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_tenant_provider_bindings FORCE ROW LEVEL SECURITY;
CREATE POLICY agent_runtime_tenant_provider_bindings_owner_all
 ON agent_runtime_tenant_provider_bindings FOR ALL TO everydayai_owner
 USING (TRUE) WITH CHECK (TRUE);
REVOKE ALL ON agent_runtime_tenant_provider_bindings FROM PUBLIC,
 everydayai_runtime,everydayai_wecom_runtime,everydayai_agent_runtime_worker,
 everydayai_worker,everydayai;

CREATE FUNCTION resolve_agent_runtime_tenant_provider_binding(
 p_catalog_revision TEXT,p_tool_name TEXT,p_scope_kind TEXT,p_scope_id TEXT,p_org_id UUID
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,public AS $$
DECLARE b agent_runtime_tenant_provider_bindings%ROWTYPE;
BEGIN
 PERFORM _assert_agent_runtime_actor(TRUE);
 IF p_scope_kind NOT IN ('user','channel')
    OR NULLIF(btrim(p_scope_id),'') IS NULL
    OR NULLIF(btrim(p_tool_name),'') IS NULL
    OR NULLIF(btrim(p_catalog_revision),'') IS NULL THEN
   RAISE EXCEPTION 'RUNTIME_TENANT_PROVIDER_BINDING_INVALID' USING ERRCODE='22023';
 END IF;
 SELECT * INTO b
 FROM agent_runtime_tenant_provider_bindings
 WHERE catalog_revision=p_catalog_revision
   AND tool_name=p_tool_name
   AND scope_kind=p_scope_kind
   AND scope_id=btrim(p_scope_id)
   AND org_id IS NOT DISTINCT FROM p_org_id;
 IF b.tool_name IS NULL THEN
   RETURN jsonb_build_object('outcome','not_found');
 END IF;
 RETURN jsonb_build_object(
   'outcome','found','tool_name',b.tool_name,
   'provider_revision',b.provider_revision,
   'credential_handle',b.credential_handle,
   'readiness_hash',b.readiness_hash,
   'service_wiring_ready',b.service_wiring_ready,
   'credential_available',b.credential_available,
   'capability_enabled',b.capability_enabled,
   'probe_passed',b.probe_passed,'ready',b.ready
 );
END $$;

REVOKE ALL ON FUNCTION resolve_agent_runtime_tenant_provider_binding(TEXT,TEXT,TEXT,TEXT,UUID)
 FROM PUBLIC;
GRANT EXECUTE ON FUNCTION resolve_agent_runtime_tenant_provider_binding(TEXT,TEXT,TEXT,TEXT,UUID)
 TO everydayai_agent_runtime_worker;

RESET ROLE;
