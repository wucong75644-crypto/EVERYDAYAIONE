-- 094: 外部 API 数据列 varchar → TEXT（根治字段超长问题）
--
-- 根因：erp_document_items 的列使用固定长度 varchar 接收快麦 API 返回数据，
--       API 返回值长度不可控，历次已出现 varchar(16)/varchar(64)/varchar(512) 溢出。
--       每次扩容治标不治本。
--
-- 根本修复：所有接收外部 API 数据的列统一改为 TEXT（无长度限制）。
--   - doc_type/platform/unified_status 保留 varchar（内部枚举，代码控制）
--   - 其余全部改 TEXT
--   - 同步变更 archive 表
--
-- PostgreSQL 中 varchar(n) → TEXT 只改元数据，不重写表，瞬间完成。
-- TEXT 与 varchar 在 PG 中存储方式完全相同，无性能差异。

BEGIN;

-- ═══════════════════════════════════════════════════════
-- 主表：erp_document_items
-- ═══════════════════════════════════════════════════════

ALTER TABLE erp_document_items
    -- 原 varchar(64)
    ALTER COLUMN num_iid TYPE TEXT,
    ALTER COLUMN refund_warehouse_id TYPE TEXT,
    ALTER COLUMN shop_user_id TYPE TEXT,
    ALTER COLUMN sku_id TYPE TEXT,
    ALTER COLUMN stock_status TYPE TEXT,
    ALTER COLUMN supplier_code TYPE TEXT,
    ALTER COLUMN warehouse_id TYPE TEXT,
    -- 原 varchar(128)
    ALTER COLUMN advance_status_text TYPE TEXT,
    ALTER COLUMN buyer_name TYPE TEXT,
    ALTER COLUMN buyer_phone TYPE TEXT,
    ALTER COLUMN handler_status_text TYPE TEXT,
    ALTER COLUMN online_status_text TYPE TEXT,
    ALTER COLUMN order_sid TYPE TEXT,
    ALTER COLUMN outer_id TYPE TEXT,
    ALTER COLUMN receiver_street TYPE TEXT,
    ALTER COLUMN refund_warehouse_name TYPE TEXT,
    ALTER COLUMN shop_name TYPE TEXT,
    ALTER COLUMN short_id TYPE TEXT,
    ALTER COLUMN sku_outer_id TYPE TEXT,
    ALTER COLUMN split_sid TYPE TEXT,
    ALTER COLUMN supplier_name TYPE TEXT,
    ALTER COLUMN warehouse_name TYPE TEXT,
    -- 原 varchar(256)
    ALTER COLUMN creator_name TYPE TEXT,
    ALTER COLUMN doc_code TYPE TEXT,
    ALTER COLUMN doc_id TYPE TEXT,
    ALTER COLUMN doc_status TYPE TEXT,
    ALTER COLUMN express_company TYPE TEXT,
    ALTER COLUMN express_no TYPE TEXT,
    ALTER COLUMN good_status TYPE TEXT,
    ALTER COLUMN item_name TYPE TEXT,
    ALTER COLUMN order_no TYPE TEXT,
    ALTER COLUMN order_status TYPE TEXT,
    ALTER COLUMN order_type TYPE TEXT,
    ALTER COLUMN platform_refund_id TYPE TEXT,
    ALTER COLUMN purchase_order_code TYPE TEXT,
    ALTER COLUMN refund_express_company TYPE TEXT,
    ALTER COLUMN refund_express_no TYPE TEXT,
    ALTER COLUMN refund_status TYPE TEXT,
    ALTER COLUMN reissue_sid TYPE TEXT,
    ALTER COLUMN sku_properties_name TYPE TEXT,
    ALTER COLUMN status_name TYPE TEXT,
    ALTER COLUMN sys_title TYPE TEXT,
    ALTER COLUMN template_name TYPE TEXT,
    ALTER COLUMN text_reason TYPE TEXT,
    ALTER COLUMN trade_warehouse_name TYPE TEXT,
    ALTER COLUMN wangwang_num TYPE TEXT,
    -- 原 varchar(512)
    ALTER COLUMN buyer_nick TYPE TEXT,
    ALTER COLUMN receiver_city TYPE TEXT,
    ALTER COLUMN receiver_district TYPE TEXT,
    ALTER COLUMN receiver_mobile TYPE TEXT,
    ALTER COLUMN receiver_name TYPE TEXT,
    ALTER COLUMN receiver_phone TYPE TEXT,
    ALTER COLUMN receiver_state TYPE TEXT,
    -- 原 varchar(1024)
    ALTER COLUMN receiver_address TYPE TEXT;

-- ═══════════════════════════════════════════════════════
-- 归档表：erp_document_items_archive
-- ═══════════════════════════════════════════════════════

ALTER TABLE erp_document_items_archive
    ALTER COLUMN num_iid TYPE TEXT,
    ALTER COLUMN refund_warehouse_id TYPE TEXT,
    ALTER COLUMN shop_user_id TYPE TEXT,
    ALTER COLUMN sku_id TYPE TEXT,
    ALTER COLUMN stock_status TYPE TEXT,
    ALTER COLUMN supplier_code TYPE TEXT,
    ALTER COLUMN warehouse_id TYPE TEXT,
    ALTER COLUMN advance_status_text TYPE TEXT,
    ALTER COLUMN buyer_name TYPE TEXT,
    ALTER COLUMN buyer_phone TYPE TEXT,
    ALTER COLUMN handler_status_text TYPE TEXT,
    ALTER COLUMN online_status_text TYPE TEXT,
    ALTER COLUMN order_sid TYPE TEXT,
    ALTER COLUMN outer_id TYPE TEXT,
    ALTER COLUMN receiver_street TYPE TEXT,
    ALTER COLUMN refund_warehouse_name TYPE TEXT,
    ALTER COLUMN shop_name TYPE TEXT,
    ALTER COLUMN short_id TYPE TEXT,
    ALTER COLUMN sku_outer_id TYPE TEXT,
    ALTER COLUMN split_sid TYPE TEXT,
    ALTER COLUMN supplier_name TYPE TEXT,
    ALTER COLUMN warehouse_name TYPE TEXT,
    ALTER COLUMN creator_name TYPE TEXT,
    ALTER COLUMN doc_code TYPE TEXT,
    ALTER COLUMN doc_id TYPE TEXT,
    ALTER COLUMN doc_status TYPE TEXT,
    ALTER COLUMN express_company TYPE TEXT,
    ALTER COLUMN express_no TYPE TEXT,
    ALTER COLUMN good_status TYPE TEXT,
    ALTER COLUMN item_name TYPE TEXT,
    ALTER COLUMN order_no TYPE TEXT,
    ALTER COLUMN order_status TYPE TEXT,
    ALTER COLUMN order_type TYPE TEXT,
    ALTER COLUMN platform_refund_id TYPE TEXT,
    ALTER COLUMN purchase_order_code TYPE TEXT,
    ALTER COLUMN refund_express_company TYPE TEXT,
    ALTER COLUMN refund_express_no TYPE TEXT,
    ALTER COLUMN refund_status TYPE TEXT,
    ALTER COLUMN reissue_sid TYPE TEXT,
    ALTER COLUMN sku_properties_name TYPE TEXT,
    ALTER COLUMN status_name TYPE TEXT,
    ALTER COLUMN sys_title TYPE TEXT,
    ALTER COLUMN template_name TYPE TEXT,
    ALTER COLUMN text_reason TYPE TEXT,
    ALTER COLUMN trade_warehouse_name TYPE TEXT,
    ALTER COLUMN wangwang_num TYPE TEXT,
    ALTER COLUMN buyer_nick TYPE TEXT,
    ALTER COLUMN receiver_city TYPE TEXT,
    ALTER COLUMN receiver_district TYPE TEXT,
    ALTER COLUMN receiver_mobile TYPE TEXT,
    ALTER COLUMN receiver_name TYPE TEXT,
    ALTER COLUMN receiver_phone TYPE TEXT,
    ALTER COLUMN receiver_state TYPE TEXT,
    ALTER COLUMN receiver_address TYPE TEXT;

COMMIT;
