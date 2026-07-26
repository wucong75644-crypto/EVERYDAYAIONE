SET LOCAL ROLE everydayai_owner;

ALTER TABLE knowledge_nodes NO FORCE ROW LEVEL SECURITY;
ALTER TABLE knowledge_edges NO FORCE ROW LEVEL SECURITY;
ALTER TABLE knowledge_metrics NO FORCE ROW LEVEL SECURITY;
ALTER TABLE scoring_audit_log NO FORCE ROW LEVEL SECURITY;
ALTER TABLE tool_audit_log NO FORCE ROW LEVEL SECURITY;

DROP POLICY knowledge_nodes_owner_all ON knowledge_nodes;
DROP POLICY knowledge_edges_owner_all ON knowledge_edges;
DROP POLICY knowledge_metrics_owner_all ON knowledge_metrics;
DROP POLICY scoring_audit_log_owner_all ON scoring_audit_log;
DROP POLICY tool_audit_log_owner_all ON tool_audit_log;

-- 197 的 Runtime 权限和 RLS policy 保持不变。
GRANT SELECT, INSERT, UPDATE ON knowledge_nodes TO everydayai_runtime;
GRANT SELECT, INSERT, UPDATE ON knowledge_edges TO everydayai_runtime;
GRANT INSERT ON knowledge_metrics TO everydayai_runtime;

RESET ROLE;
