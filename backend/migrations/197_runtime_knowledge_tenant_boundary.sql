-- 197: Knowledge Runtime 企业/散客/系统三类事实边界。
-- 暂不 FORCE RLS；Worker 模型评分能力收口后由最终迁移统一强制。

SET LOCAL ROLE everydayai_owner;

ALTER TABLE knowledge_nodes
    ADD COLUMN owner_user_id UUID REFERENCES users(id) ON DELETE CASCADE;
ALTER TABLE knowledge_edges
    ADD COLUMN owner_user_id UUID REFERENCES users(id) ON DELETE CASCADE;

UPDATE knowledge_nodes
   SET owner_user_id = substring(
       scope FROM
       '^user:([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$'
   )::UUID
 WHERE org_id IS NULL
   AND owner_user_id IS NULL
   AND scope ~
       '^user:[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$';

UPDATE knowledge_edges edge
   SET owner_user_id = node.owner_user_id
  FROM knowledge_nodes node
 WHERE edge.source_id = node.id
   AND edge.org_id IS NULL
   AND edge.owner_user_id IS NULL
   AND node.owner_user_id IS NOT NULL;

ALTER TABLE knowledge_nodes
    ADD CONSTRAINT knowledge_nodes_owner_scope_check
    CHECK (org_id IS NULL OR owner_user_id IS NULL);
ALTER TABLE knowledge_edges
    ADD CONSTRAINT knowledge_edges_owner_scope_check
    CHECK (org_id IS NULL OR owner_user_id IS NULL);

DROP INDEX uq_knowledge_nodes_org;
CREATE UNIQUE INDEX uq_knowledge_nodes_owner
ON knowledge_nodes (
    content_hash,
    COALESCE(org_id, '00000000-0000-0000-0000-000000000000'::UUID),
    COALESCE(owner_user_id, '00000000-0000-0000-0000-000000000000'::UUID)
);
CREATE INDEX idx_knowledge_nodes_owner_user
ON knowledge_nodes(owner_user_id) WHERE owner_user_id IS NOT NULL;
CREATE INDEX idx_knowledge_edges_owner_user
ON knowledge_edges(owner_user_id) WHERE owner_user_id IS NOT NULL;

CREATE FUNCTION tenant_knowledge_visible(
    p_org_id UUID,
    p_owner_user_id UUID
)
RETURNS BOOLEAN
LANGUAGE sql
STABLE
SET search_path = pg_catalog, public
AS $$
    SELECT session_user = 'everydayai_runtime'
       AND current_setting('app.access_kind', TRUE) = 'runtime'
       AND tenant_actor_user_id() IS NOT NULL
       AND (
           (p_org_id IS NULL AND p_owner_user_id IS NULL)
           OR (
               p_org_id = tenant_org_id()
               AND p_owner_user_id IS NULL
               AND tenant_actor_is_active_member(p_org_id)
           )
           OR (
               p_org_id IS NULL
               AND tenant_org_id() IS NULL
               AND p_owner_user_id = tenant_actor_user_id()
           )
       )
$$;

CREATE FUNCTION tenant_knowledge_writable(
    p_org_id UUID,
    p_owner_user_id UUID
)
RETURNS BOOLEAN
LANGUAGE sql
STABLE
SET search_path = pg_catalog, public
AS $$
    SELECT session_user = 'everydayai_runtime'
       AND current_setting('app.access_kind', TRUE) = 'runtime'
       AND tenant_actor_user_id() IS NOT NULL
       AND (
           (
               p_org_id = tenant_org_id()
               AND p_org_id IS NOT NULL
               AND p_owner_user_id IS NULL
               AND tenant_actor_is_active_member(p_org_id)
           )
           OR (
               p_org_id IS NULL
               AND tenant_org_id() IS NULL
               AND p_owner_user_id = tenant_actor_user_id()
           )
       )
$$;

ALTER TABLE knowledge_nodes ENABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge_edges ENABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge_metrics ENABLE ROW LEVEL SECURITY;

CREATE POLICY runtime_knowledge_nodes_select ON knowledge_nodes
FOR SELECT TO everydayai_runtime
USING (tenant_knowledge_visible(org_id, owner_user_id));
CREATE POLICY runtime_knowledge_nodes_insert ON knowledge_nodes
FOR INSERT TO everydayai_runtime
WITH CHECK (tenant_knowledge_writable(org_id, owner_user_id));
CREATE POLICY runtime_knowledge_nodes_update ON knowledge_nodes
FOR UPDATE TO everydayai_runtime
USING (tenant_knowledge_writable(org_id, owner_user_id))
WITH CHECK (tenant_knowledge_writable(org_id, owner_user_id));

CREATE POLICY runtime_knowledge_edges_select ON knowledge_edges
FOR SELECT TO everydayai_runtime
USING (tenant_knowledge_visible(org_id, owner_user_id));
CREATE POLICY runtime_knowledge_edges_insert ON knowledge_edges
FOR INSERT TO everydayai_runtime
WITH CHECK (
    tenant_knowledge_writable(org_id, owner_user_id)
    AND EXISTS (
        SELECT 1 FROM knowledge_nodes node
         WHERE node.id = source_id
           AND tenant_knowledge_visible(node.org_id, node.owner_user_id)
    )
    AND EXISTS (
        SELECT 1 FROM knowledge_nodes node
         WHERE node.id = target_id
           AND tenant_knowledge_visible(node.org_id, node.owner_user_id)
    )
);
CREATE POLICY runtime_knowledge_edges_update ON knowledge_edges
FOR UPDATE TO everydayai_runtime
USING (tenant_knowledge_writable(org_id, owner_user_id))
WITH CHECK (tenant_knowledge_writable(org_id, owner_user_id));

CREATE POLICY runtime_knowledge_metrics_insert ON knowledge_metrics
FOR INSERT TO everydayai_runtime
WITH CHECK (
    session_user = 'everydayai_runtime'
    AND current_setting('app.access_kind', TRUE) = 'runtime'
    AND user_id = tenant_actor_user_id()
    AND (
        (
            org_id = tenant_org_id()
            AND org_id IS NOT NULL
            AND tenant_actor_is_active_member(org_id)
        )
        OR (org_id IS NULL AND tenant_org_id() IS NULL)
    )
);

GRANT SELECT, INSERT, UPDATE ON knowledge_nodes TO everydayai_runtime;
GRANT SELECT, INSERT, UPDATE ON knowledge_edges TO everydayai_runtime;
GRANT INSERT ON knowledge_metrics TO everydayai_runtime;

REVOKE ALL ON FUNCTION tenant_knowledge_visible(UUID, UUID),
    tenant_knowledge_writable(UUID, UUID)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker, everydayai;
GRANT EXECUTE ON FUNCTION tenant_knowledge_visible(UUID, UUID),
    tenant_knowledge_writable(UUID, UUID)
TO everydayai_runtime;

RESET ROLE;
