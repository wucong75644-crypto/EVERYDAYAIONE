-- 202: Knowledge/Audit 域最终 FORCE RLS 与服务角色 ACL 收口。
-- 前置：196 Runtime 工具审计、197 Runtime 知识边界、198 Worker 评分能力已生效。

SET LOCAL ROLE everydayai_owner;

CREATE POLICY knowledge_nodes_owner_all ON knowledge_nodes
FOR ALL TO everydayai_owner USING (TRUE) WITH CHECK (TRUE);
CREATE POLICY knowledge_edges_owner_all ON knowledge_edges
FOR ALL TO everydayai_owner USING (TRUE) WITH CHECK (TRUE);
CREATE POLICY knowledge_metrics_owner_all ON knowledge_metrics
FOR ALL TO everydayai_owner USING (TRUE) WITH CHECK (TRUE);
CREATE POLICY scoring_audit_log_owner_all ON scoring_audit_log
FOR ALL TO everydayai_owner USING (TRUE) WITH CHECK (TRUE);
CREATE POLICY tool_audit_log_owner_all ON tool_audit_log
FOR ALL TO everydayai_owner USING (TRUE) WITH CHECK (TRUE);

ALTER TABLE knowledge_nodes ENABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge_edges ENABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge_metrics ENABLE ROW LEVEL SECURITY;
ALTER TABLE scoring_audit_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE tool_audit_log ENABLE ROW LEVEL SECURITY;

ALTER TABLE knowledge_nodes FORCE ROW LEVEL SECURITY;
ALTER TABLE knowledge_edges FORCE ROW LEVEL SECURITY;
ALTER TABLE knowledge_metrics FORCE ROW LEVEL SECURITY;
ALTER TABLE scoring_audit_log FORCE ROW LEVEL SECURITY;
ALTER TABLE tool_audit_log FORCE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE
    knowledge_nodes, knowledge_edges, knowledge_metrics,
    scoring_audit_log, tool_audit_log
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync;

DO $$
DECLARE
    table_name TEXT;
    column_names TEXT;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
        'knowledge_nodes', 'knowledge_edges', 'knowledge_metrics',
        'scoring_audit_log', 'tool_audit_log'
    ] LOOP
        SELECT string_agg(quote_ident(attribute.attname), ', ')
          INTO column_names
          FROM pg_catalog.pg_attribute attribute
         WHERE attribute.attrelid = ('public.' || table_name)::regclass
           AND attribute.attnum > 0
           AND NOT attribute.attisdropped;
        EXECUTE format(
            'REVOKE ALL PRIVILEGES (%s) ON TABLE public.%I FROM '
            'everydayai_runtime, everydayai_wecom_runtime, '
            'everydayai_worker, everydayai_sync',
            column_names, table_name
        );
    END LOOP;
END;
$$;

-- Runtime 保留 197 已定义、受 RLS 限制的知识读写面。
GRANT SELECT, INSERT, UPDATE ON knowledge_nodes TO everydayai_runtime;
GRANT SELECT, INSERT, UPDATE ON knowledge_edges TO everydayai_runtime;
GRANT INSERT ON knowledge_metrics TO everydayai_runtime;

RESET ROLE;
