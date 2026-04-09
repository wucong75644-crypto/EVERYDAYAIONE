-- 052: ERP 唯一索引加 org_id（多租户隔离）
-- 多租户隔离架构 P3 — 技术方案 §6.2
--
-- ⚠️ 必须在维护窗口执行！
-- ⚠️ 执行前暂停 ERP 同步（DROP INDEX/CONSTRAINT 到 CREATE INDEX 之间 upsert 会失败）
-- ⚠️ 预计耗时 1-3 分钟（取决于表大小）
--
-- 前提：051 已为缺失的表补 org_id 列。
-- 策略：ERP 表 org_id 设 NOT NULL（OrgScopedDB 保证写入时有值），
--        使用简单列索引（PostgREST on_conflict 可直接匹配）。
-- knowledge_nodes / credit_transactions 保留 COALESCE（有合法 NULL 数据）。
--
-- 回滚方式：DROP 新索引 + CREATE 旧索引/约束（名称在注释中标注）

-- ============================================================
-- 先填充残留 NULL 并设 NOT NULL（确保简单列索引不冲突）
-- ============================================================
UPDATE erp_product_skus SET org_id = (SELECT id FROM organizations LIMIT 1) WHERE org_id IS NULL;
UPDATE erp_product_daily_stats SET org_id = (SELECT id FROM organizations LIMIT 1) WHERE org_id IS NULL;

-- ============================================================
-- 1. erp_products (旧: erp_products_outer_id_key 约束)
-- ============================================================
ALTER TABLE erp_products DROP CONSTRAINT IF EXISTS erp_products_outer_id_key;
DROP INDEX IF EXISTS uq_products_org;
CREATE UNIQUE INDEX uq_products_org ON erp_products (outer_id, org_id);
ALTER TABLE erp_products ALTER COLUMN org_id SET NOT NULL;

-- ============================================================
-- 2. erp_product_skus (旧: erp_product_skus_sku_outer_id_key 约束)
-- ============================================================
ALTER TABLE erp_product_skus DROP CONSTRAINT IF EXISTS erp_product_skus_sku_outer_id_key;
DROP INDEX IF EXISTS uq_product_skus_org;
CREATE UNIQUE INDEX uq_product_skus_org ON erp_product_skus (sku_outer_id, org_id);
ALTER TABLE erp_product_skus ALTER COLUMN org_id SET NOT NULL;

-- ============================================================
-- 3. erp_stock_status (旧: uq_stock_outer_sku)
-- ============================================================
DROP INDEX IF EXISTS uq_stock_outer_sku;
DROP INDEX IF EXISTS uq_stock_org;
CREATE UNIQUE INDEX uq_stock_org ON erp_stock_status (outer_id, sku_outer_id, warehouse_id, org_id);
ALTER TABLE erp_stock_status ALTER COLUMN org_id SET NOT NULL;

-- ============================================================
-- 4. erp_document_items (旧: uq_doc_items)
-- ============================================================
DROP INDEX IF EXISTS uq_doc_items;
DROP INDEX IF EXISTS uq_doc_items_org;
CREATE UNIQUE INDEX uq_doc_items_org ON erp_document_items (doc_type, doc_id, item_index, org_id);
ALTER TABLE erp_document_items ALTER COLUMN org_id SET NOT NULL;

-- ============================================================
-- 5. erp_document_items_archive (旧: uq_archive_items)
-- ============================================================
DROP INDEX IF EXISTS uq_archive_items;
DROP INDEX IF EXISTS uq_archive_items_org;
CREATE UNIQUE INDEX uq_archive_items_org ON erp_document_items_archive (doc_type, doc_id, item_index, org_id);

-- ============================================================
-- 6. erp_product_daily_stats (旧: uq_daily_stats)
-- ============================================================
DROP INDEX IF EXISTS uq_daily_stats;
DROP INDEX IF EXISTS uq_daily_stats_org;
CREATE UNIQUE INDEX uq_daily_stats_org ON erp_product_daily_stats (stat_date, outer_id, COALESCE(sku_outer_id, ''), org_id);
ALTER TABLE erp_product_daily_stats ALTER COLUMN org_id SET NOT NULL;

-- ============================================================
-- 7. erp_product_platform_map (旧: uq_platform_map)
-- ============================================================
DROP INDEX IF EXISTS uq_platform_map;
DROP INDEX IF EXISTS uq_platform_map_org;
CREATE UNIQUE INDEX uq_platform_map_org ON erp_product_platform_map (outer_id, num_iid, org_id);
ALTER TABLE erp_product_platform_map ALTER COLUMN org_id SET NOT NULL;

-- ============================================================
-- 8. erp_suppliers (旧: erp_suppliers_code_key 约束)
-- ============================================================
ALTER TABLE erp_suppliers DROP CONSTRAINT IF EXISTS erp_suppliers_code_key;
DROP INDEX IF EXISTS uq_suppliers_org;
CREATE UNIQUE INDEX uq_suppliers_org ON erp_suppliers (code, org_id);
ALTER TABLE erp_suppliers ALTER COLUMN org_id SET NOT NULL;

-- ============================================================
-- 9. erp_sync_dead_letter (旧: uq_dead_letter_doc)
-- ============================================================
DROP INDEX IF EXISTS uq_dead_letter_doc;
DROP INDEX IF EXISTS uq_dead_letter_org;
CREATE UNIQUE INDEX uq_dead_letter_org ON erp_sync_dead_letter (doc_type, doc_id, org_id);

-- ============================================================
-- 10. knowledge_nodes (旧: knowledge_nodes_content_hash_key 约束)
-- 注意：保留 COALESCE（全局 seed 知识 org_id=NULL 是合法的）
-- ============================================================
ALTER TABLE knowledge_nodes DROP CONSTRAINT IF EXISTS knowledge_nodes_content_hash_key;
DROP INDEX IF EXISTS uq_knowledge_nodes_org;
CREATE UNIQUE INDEX uq_knowledge_nodes_org ON knowledge_nodes (
    content_hash,
    COALESCE(org_id, '00000000-0000-0000-0000-000000000000'::uuid)
);

-- ============================================================
-- 11. credit_transactions (旧: idx_credit_tx_task_unique)
-- 注意：保留 COALESCE（散客交易 org_id=NULL 是合法的）
-- ============================================================
DROP INDEX IF EXISTS idx_credit_tx_task_unique;
DROP INDEX IF EXISTS uq_credit_tx_task_org;
CREATE UNIQUE INDEX uq_credit_tx_task_org ON credit_transactions (
    task_id,
    COALESCE(org_id, '00000000-0000-0000-0000-000000000000'::uuid)
);
