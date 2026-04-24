-- 099: 维度 ID 补全 Phase 2 — 历史数据回填
--
-- 通过名称反查维度表，为历史记录补全 warehouse_id / supplier_code / shop_user_id。
-- 前置条件：097（类型对齐+shop user_id列）、098（新增列）已执行。

-- ── 1. warehouse_id 回填（名称反查，无同名问题）──

UPDATE erp_document_items d
SET warehouse_id = w.warehouse_id
FROM erp_warehouses w
WHERE d.warehouse_name = w.name
  AND d.org_id = w.org_id
  AND d.warehouse_id IS NULL
  AND d.warehouse_name IS NOT NULL;

-- 归档表
UPDATE erp_document_items_archive d
SET warehouse_id = w.warehouse_id
FROM erp_warehouses w
WHERE d.warehouse_name = w.name
  AND d.org_id = w.org_id
  AND d.warehouse_id IS NULL
  AND d.warehouse_name IS NOT NULL;

-- ── 2. supplier_code 回填（名称反查，无同名问题）──

UPDATE erp_document_items d
SET supplier_code = s.code
FROM erp_suppliers s
WHERE d.supplier_name = s.name
  AND d.org_id = s.org_id
  AND d.supplier_code IS NULL
  AND d.supplier_name IS NOT NULL;

-- 归档表
UPDATE erp_document_items_archive d
SET supplier_code = s.code
FROM erp_suppliers s
WHERE d.supplier_name = s.name
  AND d.org_id = s.org_id
  AND d.supplier_code IS NULL
  AND d.supplier_name IS NOT NULL;

-- ── 3. shop_user_id 回填（shop_name + platform 双条件，避开同名问题）──

UPDATE erp_document_items d
SET shop_user_id = sh.user_id
FROM erp_shops sh
WHERE d.shop_name = sh.name
  AND d.platform = sh.platform
  AND d.org_id = sh.org_id
  AND d.shop_user_id IS NULL
  AND d.shop_name IS NOT NULL
  AND sh.user_id IS NOT NULL;

-- 归档表
UPDATE erp_document_items_archive d
SET shop_user_id = sh.user_id
FROM erp_shops sh
WHERE d.shop_name = sh.name
  AND d.platform = sh.platform
  AND d.org_id = sh.org_id
  AND d.shop_user_id IS NULL
  AND d.shop_name IS NOT NULL
  AND sh.user_id IS NOT NULL;
