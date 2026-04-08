-- 052: 唯一索引加 org_id（多租户隔离）
-- 多租户隔离架构 P3 — 技术方案 §6.2
--
-- ⚠️ 必须在维护窗口执行！
-- ⚠️ 执行前暂停 ERP 同步（DROP INDEX 到 CREATE INDEX 之间 upsert 会失败）
-- ⚠️ 预计耗时 1-3 分钟（取决于表大小）
--
-- 使用 COALESCE 处理 NULL org_id（散客用户），确保 UNIQUE 约束正确。
-- PostgREST upsert on_conflict 无法匹配表达式索引，
-- 但 OrgScopedDB._inject_org_id() 确保写入时 org_id 总有值。
--
-- ⚠️ 部署后验证：在测试环境执行一轮 ERP 同步，确认 PostgREST upsert
-- 能自动匹配单一唯一索引（DROP 旧约束后每表只剩 1 个唯一索引）。
-- 如果 PostgREST 报 "could not determine which unique constraint" 错误，
-- 需回退到旧索引并改用简单列索引方案（要求散客 org_id 用零值 UUID 而非 NULL）。
--
-- 回滚方式：DROP 新索引 + CREATE 旧索引（名称在注释中标注）

-- ============================================================
-- 1. erp_products (旧索引: erp_products_outer_id_key)
-- ============================================================
DROP INDEX IF EXISTS erp_products_outer_id_key;
CREATE UNIQUE INDEX uq_products_org ON erp_products (
    outer_id, COALESCE(org_id, '00000000-0000-0000-0000-000000000000'::uuid)
);

-- ============================================================
-- 2. erp_product_skus (旧索引: erp_product_skus_sku_outer_id_key)
-- ============================================================
DROP INDEX IF EXISTS erp_product_skus_sku_outer_id_key;
CREATE UNIQUE INDEX uq_product_skus_org ON erp_product_skus (
    sku_outer_id, COALESCE(org_id, '00000000-0000-0000-0000-000000000000'::uuid)
);

-- ============================================================
-- 3. erp_stock_status (旧索引: uq_stock_outer_sku)
-- ============================================================
DROP INDEX IF EXISTS uq_stock_outer_sku;
CREATE UNIQUE INDEX uq_stock_org ON erp_stock_status (
    outer_id, sku_outer_id, warehouse_id,
    COALESCE(org_id, '00000000-0000-0000-0000-000000000000'::uuid)
);

-- ============================================================
-- 4. erp_document_items (旧索引: uq_doc_items)
-- ============================================================
DROP INDEX IF EXISTS uq_doc_items;
CREATE UNIQUE INDEX uq_doc_items_org ON erp_document_items (
    doc_type, doc_id, item_index,
    COALESCE(org_id, '00000000-0000-0000-0000-000000000000'::uuid)
);

-- ============================================================
-- 5. erp_document_items_archive (旧索引: uq_archive_items)
-- ============================================================
DROP INDEX IF EXISTS uq_archive_items;
CREATE UNIQUE INDEX uq_archive_items_org ON erp_document_items_archive (
    doc_type, doc_id, item_index,
    COALESCE(org_id, '00000000-0000-0000-0000-000000000000'::uuid)
);

-- ============================================================
-- 6. erp_product_daily_stats (旧索引: uq_daily_stats)
-- ============================================================
-- ⚠️ 此表的唯一索引比较特殊：
-- erp_aggregate_daily_stats 函数用 ON CONFLICT (stat_date, outer_id, COALESCE(sku_outer_id, ''))
-- 必须保持原有列定义不变，只追加 org_id 维度
DROP INDEX IF EXISTS uq_daily_stats;
CREATE UNIQUE INDEX uq_daily_stats_org ON erp_product_daily_stats (
    stat_date, outer_id, COALESCE(sku_outer_id, ''),
    COALESCE(org_id, '00000000-0000-0000-0000-000000000000'::uuid)
);

-- ============================================================
-- 7. erp_product_platform_map (旧索引: uq_platform_map)
-- ============================================================
DROP INDEX IF EXISTS uq_platform_map;
CREATE UNIQUE INDEX uq_platform_map_org ON erp_product_platform_map (
    outer_id, num_iid,
    COALESCE(org_id, '00000000-0000-0000-0000-000000000000'::uuid)
);

-- ============================================================
-- 8. erp_suppliers (旧索引: erp_suppliers_code_key)
-- ============================================================
DROP INDEX IF EXISTS erp_suppliers_code_key;
CREATE UNIQUE INDEX uq_suppliers_org ON erp_suppliers (
    code, COALESCE(org_id, '00000000-0000-0000-0000-000000000000'::uuid)
);

-- ============================================================
-- 9. erp_sync_dead_letter (旧索引: uq_dead_letter_doc)
-- ============================================================
DROP INDEX IF EXISTS uq_dead_letter_doc;
CREATE UNIQUE INDEX uq_dead_letter_org ON erp_sync_dead_letter (
    doc_type, doc_id,
    COALESCE(org_id, '00000000-0000-0000-0000-000000000000'::uuid)
);

-- ============================================================
-- 10. knowledge_nodes (旧索引: knowledge_nodes_content_hash_key)
-- ============================================================
DROP INDEX IF EXISTS knowledge_nodes_content_hash_key;
CREATE UNIQUE INDEX uq_knowledge_nodes_org ON knowledge_nodes (
    content_hash,
    COALESCE(org_id, '00000000-0000-0000-0000-000000000000'::uuid)
);

-- ============================================================
-- 11. credit_transactions (旧索引: idx_credit_tx_task_unique)
-- ============================================================
DROP INDEX IF EXISTS idx_credit_tx_task_unique;
CREATE UNIQUE INDEX uq_credit_tx_task_org ON credit_transactions (
    task_id,
    COALESCE(org_id, '00000000-0000-0000-0000-000000000000'::uuid)
);
