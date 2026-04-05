-- ============================================================
-- 042: erp_document_items 加 order_type 列
--
-- 存储订单类型（逗号分隔多值，如 "2,3,0" "4,5,14"）
-- 含14=补发, 含8=拆分, 含7=合并, 含33=分销, 含99=出库单
-- 解决补发单被统计为成交订单的问题
-- ============================================================

-- 热表加列
ALTER TABLE erp_document_items
  ADD COLUMN IF NOT EXISTS order_type VARCHAR(64);

-- 冷表加列
ALTER TABLE erp_document_items_archive
  ADD COLUMN IF NOT EXISTS order_type VARCHAR(64);

-- 回填：从 extra_json 提取 type 字段
UPDATE erp_document_items
SET order_type = extra_json->>'type'
WHERE doc_type = 'order'
  AND extra_json->>'type' IS NOT NULL
  AND order_type IS NULL;

COMMENT ON COLUMN erp_document_items.order_type IS '订单类型（逗号分隔多值）: 含14=补发, 含8=拆分, 含7=合并, 含33=分销';
