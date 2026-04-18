-- 084: 加宽偏窄的 varchar 列 + bigint→varchar 修复
--
-- 问题根因：
--   1. varchar(64) 对外部 API 数据偏窄（售后单 wangwang_num 已见 43 字符，
--      快递单号/平台ID等随时可能超 64）
--   2. sku_id/num_iid 定义为 bigint，但快麦 API 偶尔返回 MD5 哈希字符串
--      （如 "2c0054ad6f8f9b80b86bcd87b012ddbb"），导致写入失败
--
-- 变更策略：
--   - 接收外部 API 数据的 varchar(64) → varchar(256)
--   - 接收外部 API 数据的 varchar(32) → varchar(128)
--   - sku_id / num_iid: bigint → varchar(64)
--   - 内部可控列不动（doc_type/platform/unified_status/stock_status）
--   - 同步变更 archive 表
--
-- PostgreSQL 中 ALTER TYPE varchar(n) → varchar(m) (m>n) 只改元数据，不重写表，瞬间完成。
-- bigint → varchar 需要重写列，但 erp_document_items 数据量可控。

BEGIN;

-- ═══════════════════════════════════════════════════════
-- 1. varchar(64) → varchar(256)：接收外部 API 数据的列
-- ═══════════════════════════════════════════════════════

-- 主表
ALTER TABLE erp_document_items
    ALTER COLUMN doc_id              TYPE varchar(256),
    ALTER COLUMN doc_code             TYPE varchar(256),
    ALTER COLUMN doc_status           TYPE varchar(256),
    ALTER COLUMN order_no             TYPE varchar(256),
    ALTER COLUMN order_status         TYPE varchar(256),
    ALTER COLUMN express_no           TYPE varchar(256),
    ALTER COLUMN express_company      TYPE varchar(256),
    ALTER COLUMN refund_status        TYPE varchar(256),
    ALTER COLUMN purchase_order_code  TYPE varchar(256),
    ALTER COLUMN creator_name         TYPE varchar(256),
    ALTER COLUMN order_type           TYPE varchar(256),
    ALTER COLUMN good_status          TYPE varchar(256),
    ALTER COLUMN refund_express_company TYPE varchar(256),
    ALTER COLUMN refund_express_no    TYPE varchar(256),
    ALTER COLUMN reissue_sid          TYPE varchar(256),
    ALTER COLUMN platform_refund_id   TYPE varchar(256),
    ALTER COLUMN status_name          TYPE varchar(256),
    ALTER COLUMN template_name        TYPE varchar(256),
    ALTER COLUMN wangwang_num         TYPE varchar(256),
    ALTER COLUMN trade_warehouse_name TYPE varchar(256);

-- 归档表
ALTER TABLE erp_document_items_archive
    ALTER COLUMN doc_id              TYPE varchar(256),
    ALTER COLUMN doc_code             TYPE varchar(256),
    ALTER COLUMN doc_status           TYPE varchar(256),
    ALTER COLUMN order_no             TYPE varchar(256),
    ALTER COLUMN order_status         TYPE varchar(256),
    ALTER COLUMN express_no           TYPE varchar(256),
    ALTER COLUMN express_company      TYPE varchar(256),
    ALTER COLUMN refund_status        TYPE varchar(256),
    ALTER COLUMN purchase_order_code  TYPE varchar(256),
    ALTER COLUMN creator_name         TYPE varchar(256),
    ALTER COLUMN order_type           TYPE varchar(256),
    ALTER COLUMN good_status          TYPE varchar(256),
    ALTER COLUMN refund_express_company TYPE varchar(256),
    ALTER COLUMN refund_express_no    TYPE varchar(256),
    ALTER COLUMN reissue_sid          TYPE varchar(256),
    ALTER COLUMN platform_refund_id   TYPE varchar(256),
    ALTER COLUMN status_name          TYPE varchar(256),
    ALTER COLUMN template_name        TYPE varchar(256),
    ALTER COLUMN wangwang_num         TYPE varchar(256),
    ALTER COLUMN trade_warehouse_name TYPE varchar(256);


-- ═══════════════════════════════════════════════════════
-- 2. varchar(32) → varchar(128)：接收外部 API 数据的列
-- ═══════════════════════════════════════════════════════

-- 主表
ALTER TABLE erp_document_items
    ALTER COLUMN short_id             TYPE varchar(128),
    ALTER COLUMN split_sid            TYPE varchar(128),
    ALTER COLUMN order_sid            TYPE varchar(128),
    ALTER COLUMN buyer_phone          TYPE varchar(128),
    ALTER COLUMN online_status_text   TYPE varchar(128),
    ALTER COLUMN handler_status_text  TYPE varchar(128),
    ALTER COLUMN advance_status_text  TYPE varchar(128);

-- 归档表
ALTER TABLE erp_document_items_archive
    ALTER COLUMN short_id             TYPE varchar(128),
    ALTER COLUMN split_sid            TYPE varchar(128),
    ALTER COLUMN order_sid            TYPE varchar(128),
    ALTER COLUMN buyer_phone          TYPE varchar(128),
    ALTER COLUMN online_status_text   TYPE varchar(128),
    ALTER COLUMN handler_status_text  TYPE varchar(128),
    ALTER COLUMN advance_status_text  TYPE varchar(128);


-- ═══════════════════════════════════════════════════════
-- 3. bigint → varchar(64)：API 可能返回 MD5 哈希
-- ═══════════════════════════════════════════════════════

-- 主表
ALTER TABLE erp_document_items
    ALTER COLUMN sku_id  TYPE varchar(64) USING sku_id::text,
    ALTER COLUMN num_iid TYPE varchar(64) USING num_iid::text;

-- 归档表
ALTER TABLE erp_document_items_archive
    ALTER COLUMN sku_id  TYPE varchar(64) USING sku_id::text,
    ALTER COLUMN num_iid TYPE varchar(64) USING num_iid::text;

COMMIT;
