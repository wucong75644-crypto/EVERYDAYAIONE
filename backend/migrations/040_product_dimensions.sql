-- 040: 商品尺寸字段 — 从 extra_json 提升为独立列，支持体积计算和公摊
-- 单位: cm

-- SPU 表
ALTER TABLE erp_products ADD COLUMN IF NOT EXISTS length DECIMAL(10,2);
ALTER TABLE erp_products ADD COLUMN IF NOT EXISTS width  DECIMAL(10,2);
ALTER TABLE erp_products ADD COLUMN IF NOT EXISTS height DECIMAL(10,2);

-- SKU 表
ALTER TABLE erp_product_skus ADD COLUMN IF NOT EXISTS length DECIMAL(10,2);
ALTER TABLE erp_product_skus ADD COLUMN IF NOT EXISTS width  DECIMAL(10,2);
ALTER TABLE erp_product_skus ADD COLUMN IF NOT EXISTS height DECIMAL(10,2);

-- 回填: 将已有 extra_json 中的 x/y/z 迁移到独立列
UPDATE erp_products SET
    length = (extra_json->>'x')::DECIMAL,
    width  = (extra_json->>'y')::DECIMAL,
    height = (extra_json->>'z')::DECIMAL
WHERE extra_json->>'x' IS NOT NULL;

UPDATE erp_product_skus SET
    length = (extra_json->>'x')::DECIMAL,
    width  = (extra_json->>'y')::DECIMAL,
    height = (extra_json->>'z')::DECIMAL
WHERE extra_json->>'x' IS NOT NULL;
