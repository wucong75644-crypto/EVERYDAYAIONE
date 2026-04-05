-- ============================================================
-- 044: 商品/SKU/库存表从 extra_json 提取独立列
--
-- erp_products: classify_name, seller_cat_name
-- erp_product_skus: sku_remark
-- erp_stock_status: cid_name
-- ============================================================

-- A. 商品表
ALTER TABLE erp_products ADD COLUMN IF NOT EXISTS classify_name VARCHAR(64);
ALTER TABLE erp_products ADD COLUMN IF NOT EXISTS seller_cat_name VARCHAR(256);

-- B. SKU表
ALTER TABLE erp_product_skus ADD COLUMN IF NOT EXISTS sku_remark TEXT;

-- C. 库存表
ALTER TABLE erp_stock_status ADD COLUMN IF NOT EXISTS cid_name VARCHAR(64);

-- D. 回填
-- D1. classify_name（从 JSON 对象提取 name）
UPDATE erp_products
SET classify_name = extra_json->'classify'->>'name'
WHERE extra_json->'classify'->>'name' IS NOT NULL
  AND classify_name IS NULL;

-- D2. seller_cat_name（从 JSON 数组提取第一个分类的 fullName）
UPDATE erp_products
SET seller_cat_name = extra_json->'sellerCats'->0->>'fullName'
WHERE extra_json->'sellerCats' IS NOT NULL
  AND jsonb_array_length(extra_json->'sellerCats') > 0
  AND seller_cat_name IS NULL;

-- D3. sku_remark
UPDATE erp_product_skus
SET sku_remark = extra_json->>'skuRemark'
WHERE extra_json->>'skuRemark' IS NOT NULL
  AND extra_json->>'skuRemark' != ''
  AND sku_remark IS NULL;

-- D4. cid_name
UPDATE erp_stock_status
SET cid_name = extra_json->>'cidName'
WHERE extra_json->>'cidName' IS NOT NULL
  AND cid_name IS NULL;

-- E. 注释
COMMENT ON COLUMN erp_products.classify_name IS '商品分类名称（如纸制品类/百货/亚克力）';
COMMENT ON COLUMN erp_products.seller_cat_name IS '卖家自定义分类（fullName，如["纸制品类","笔记本"]）';
COMMENT ON COLUMN erp_product_skus.sku_remark IS 'SKU备注';
COMMENT ON COLUMN erp_stock_status.cid_name IS '分类名称';
