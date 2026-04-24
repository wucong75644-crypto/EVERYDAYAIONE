-- 097: 维度 ID 补全 Phase 0 — 类型对齐 + 店铺表加 user_id 列
--
-- 问题 1: erp_document_items.warehouse_id 是 INTEGER，
--          erp_warehouses.warehouse_id 是 VARCHAR(64)，无法 JOIN。
-- 问题 2: erp_shops 的 userId 只存在 extra_json 中，无法高效 JOIN。
--
-- 修复：
--   1. warehouse_id / refund_warehouse_id 从 INTEGER 改为 VARCHAR(64)
--   2. erp_shops 新增 user_id 物理列 + 索引

-- ── 1. warehouse_id 类型对齐 ──

ALTER TABLE erp_document_items
    ALTER COLUMN warehouse_id TYPE VARCHAR(64) USING warehouse_id::VARCHAR;

ALTER TABLE erp_document_items
    ALTER COLUMN refund_warehouse_id TYPE VARCHAR(64) USING refund_warehouse_id::VARCHAR;

-- 归档表同步
ALTER TABLE erp_document_items_archive
    ALTER COLUMN warehouse_id TYPE VARCHAR(64) USING warehouse_id::VARCHAR;

ALTER TABLE erp_document_items_archive
    ALTER COLUMN refund_warehouse_id TYPE VARCHAR(64) USING refund_warehouse_id::VARCHAR;

-- ── 2. erp_shops 新增 user_id 列 ──

ALTER TABLE erp_shops ADD COLUMN IF NOT EXISTS user_id VARCHAR(64);

-- 从 extra_json 提取 userId 回填
UPDATE erp_shops
SET user_id = extra_json->>'userId'
WHERE extra_json->>'userId' IS NOT NULL
  AND user_id IS NULL;

-- 索引：用于 JOIN erp_document_items.shop_user_id
CREATE INDEX IF NOT EXISTS idx_erp_shops_user_id
    ON erp_shops (user_id, org_id) WHERE user_id IS NOT NULL;
