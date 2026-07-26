SET LOCAL ROLE everydayai_owner;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM knowledge_nodes WHERE owner_user_id IS NOT NULL
        UNION ALL
        SELECT 1 FROM knowledge_edges WHERE owner_user_id IS NOT NULL
    ) THEN
        RAISE EXCEPTION 'KNOWLEDGE_PERSONAL_FACTS_REQUIRE_FORWARD_ROLLBACK';
    END IF;
END;
$$;

REVOKE SELECT, INSERT, UPDATE ON knowledge_nodes FROM everydayai_runtime;
REVOKE SELECT, INSERT, UPDATE ON knowledge_edges FROM everydayai_runtime;
REVOKE INSERT ON knowledge_metrics FROM everydayai_runtime;

DROP POLICY runtime_knowledge_nodes_select ON knowledge_nodes;
DROP POLICY runtime_knowledge_nodes_insert ON knowledge_nodes;
DROP POLICY runtime_knowledge_nodes_update ON knowledge_nodes;
DROP POLICY runtime_knowledge_edges_select ON knowledge_edges;
DROP POLICY runtime_knowledge_edges_insert ON knowledge_edges;
DROP POLICY runtime_knowledge_edges_update ON knowledge_edges;
DROP POLICY runtime_knowledge_metrics_insert ON knowledge_metrics;

ALTER TABLE knowledge_nodes DISABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge_edges DISABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge_metrics DISABLE ROW LEVEL SECURITY;

DROP FUNCTION tenant_knowledge_visible(UUID, UUID);
DROP FUNCTION tenant_knowledge_writable(UUID, UUID);

DROP INDEX uq_knowledge_nodes_owner;
DROP INDEX idx_knowledge_nodes_owner_user;
DROP INDEX idx_knowledge_edges_owner_user;
CREATE UNIQUE INDEX uq_knowledge_nodes_org ON knowledge_nodes (
    content_hash,
    COALESCE(org_id, '00000000-0000-0000-0000-000000000000'::UUID)
);

ALTER TABLE knowledge_edges DROP CONSTRAINT knowledge_edges_owner_scope_check;
ALTER TABLE knowledge_nodes DROP CONSTRAINT knowledge_nodes_owner_scope_check;
ALTER TABLE knowledge_edges DROP COLUMN owner_user_id;
ALTER TABLE knowledge_nodes DROP COLUMN owner_user_id;

RESET ROLE;
