-- 补全 knowledge_metrics 的 org_id 索引（039 多租户迁移遗漏）
-- 根因：model_scorer 每小时聚合查询 WHERE org_id = ? AND created_at > ?
-- 缺少此索引导致全表回表扫描，连接超时

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_knowledge_metrics_org
    ON knowledge_metrics(org_id, created_at DESC)
    WHERE org_id IS NOT NULL;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_knowledge_metrics_org_null
    ON knowledge_metrics(created_at DESC)
    WHERE org_id IS NULL;
