-- 050: 修复 upsert 唯一索引（PostgREST 不支持 COALESCE 表达式）
-- 改为 org_id NOT NULL DEFAULT + 简单列唯一索引

-- ── 设置 org_id 默认值并填充 ─────────────────────────────
ALTER TABLE erp_shops ALTER COLUMN org_id SET DEFAULT '00000000-0000-0000-0000-000000000000'::UUID;
UPDATE erp_shops SET org_id = '00000000-0000-0000-0000-000000000000' WHERE org_id IS NULL;

ALTER TABLE erp_warehouses ALTER COLUMN org_id SET DEFAULT '00000000-0000-0000-0000-000000000000'::UUID;
UPDATE erp_warehouses SET org_id = '00000000-0000-0000-0000-000000000000' WHERE org_id IS NULL;

ALTER TABLE erp_tags ALTER COLUMN org_id SET DEFAULT '00000000-0000-0000-0000-000000000000'::UUID;
UPDATE erp_tags SET org_id = '00000000-0000-0000-0000-000000000000' WHERE org_id IS NULL;

ALTER TABLE erp_categories ALTER COLUMN org_id SET DEFAULT '00000000-0000-0000-0000-000000000000'::UUID;
UPDATE erp_categories SET org_id = '00000000-0000-0000-0000-000000000000' WHERE org_id IS NULL;

ALTER TABLE erp_logistics_companies ALTER COLUMN org_id SET DEFAULT '00000000-0000-0000-0000-000000000000'::UUID;
UPDATE erp_logistics_companies SET org_id = '00000000-0000-0000-0000-000000000000' WHERE org_id IS NULL;

ALTER TABLE erp_order_logs ALTER COLUMN org_id SET DEFAULT '00000000-0000-0000-0000-000000000000'::UUID;
ALTER TABLE erp_order_logs ALTER COLUMN action SET DEFAULT '';
ALTER TABLE erp_order_logs ALTER COLUMN operate_time SET DEFAULT '1970-01-01';

ALTER TABLE erp_order_packages ALTER COLUMN org_id SET DEFAULT '00000000-0000-0000-0000-000000000000'::UUID;
ALTER TABLE erp_order_packages ALTER COLUMN express_no SET DEFAULT '';
ALTER TABLE erp_order_packages ALTER COLUMN package_id SET DEFAULT '';

ALTER TABLE erp_aftersale_logs ALTER COLUMN org_id SET DEFAULT '00000000-0000-0000-0000-000000000000'::UUID;
ALTER TABLE erp_aftersale_logs ALTER COLUMN action SET DEFAULT '';
ALTER TABLE erp_aftersale_logs ALTER COLUMN operate_time SET DEFAULT '1970-01-01';

ALTER TABLE erp_batch_stock ALTER COLUMN org_id SET DEFAULT '00000000-0000-0000-0000-000000000000'::UUID;
ALTER TABLE erp_batch_stock ALTER COLUMN batch_no SET DEFAULT '';
ALTER TABLE erp_batch_stock ALTER COLUMN shop_id SET DEFAULT '';
ALTER TABLE erp_batch_stock ALTER COLUMN warehouse_name SET DEFAULT '';

-- ── 替换唯一索引（去掉 COALESCE）─────────────────────────
DROP INDEX IF EXISTS uq_erp_shops;
CREATE UNIQUE INDEX uq_erp_shops ON erp_shops (shop_id, org_id);

DROP INDEX IF EXISTS uq_erp_warehouses;
CREATE UNIQUE INDEX uq_erp_warehouses ON erp_warehouses (warehouse_id, org_id);

DROP INDEX IF EXISTS uq_erp_tags;
CREATE UNIQUE INDEX uq_erp_tags ON erp_tags (tag_id, tag_source, org_id);

DROP INDEX IF EXISTS uq_erp_categories;
CREATE UNIQUE INDEX uq_erp_categories ON erp_categories (cat_id, cat_source, org_id);

DROP INDEX IF EXISTS uq_erp_logistics_companies;
CREATE UNIQUE INDEX uq_erp_logistics_companies ON erp_logistics_companies (company_id, org_id);

DROP INDEX IF EXISTS uq_erp_order_logs;
CREATE UNIQUE INDEX uq_erp_order_logs ON erp_order_logs (system_id, operate_time, action, org_id);

DROP INDEX IF EXISTS uq_erp_order_packages;
CREATE UNIQUE INDEX uq_erp_order_packages ON erp_order_packages (system_id, express_no, package_id, org_id);

DROP INDEX IF EXISTS uq_erp_aftersale_logs;
CREATE UNIQUE INDEX uq_erp_aftersale_logs ON erp_aftersale_logs (work_order_id, operate_time, action, org_id);

DROP INDEX IF EXISTS uq_erp_batch_stock;
CREATE UNIQUE INDEX uq_erp_batch_stock ON erp_batch_stock (outer_id, sku_outer_id, batch_no, shop_id, warehouse_name, org_id);
