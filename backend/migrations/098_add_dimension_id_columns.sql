-- 098: 维度 ID 补全 Phase 1 — 新增 supplier_code / shop_user_id 列
--
-- 事实表新增两个维度 ID 列，用于 JOIN 维度表取最新名称。
-- supplier_code → erp_suppliers.code
-- shop_user_id → erp_shops.user_id（快麦 API 的 userId）

-- ── 事实表 ──
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS supplier_code VARCHAR(64);
ALTER TABLE erp_document_items ADD COLUMN IF NOT EXISTS shop_user_id VARCHAR(64);

-- ── 归档表 ──
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS supplier_code VARCHAR(64);
ALTER TABLE erp_document_items_archive ADD COLUMN IF NOT EXISTS shop_user_id VARCHAR(64);

-- ── 索引（用于 JOIN + 过滤查询）──
CREATE INDEX IF NOT EXISTS idx_doc_items_supplier_code
    ON erp_document_items (supplier_code) WHERE supplier_code IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_doc_items_shop_user_id
    ON erp_document_items (shop_user_id) WHERE shop_user_id IS NOT NULL;
