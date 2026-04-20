-- 087: 加宽 erp_document_items.stock_status varchar(16) → varchar(64)
--
-- 问题根因：
--   084 迁移将 stock_status 归类为"内部可控列"而跳过加宽，
--   但实际数据来自快麦 API doc.stockStatus，属于外部数据。
--   API 返回值可能超过 16 字符，导致 upsert_document_items 持续报错。
--
-- 影响：erp_document_items + erp_document_items_archive
-- PostgreSQL ALTER TYPE varchar(n)→varchar(m) (m>n) 只改元数据，瞬间完成。

BEGIN;

ALTER TABLE erp_document_items
    ALTER COLUMN stock_status TYPE varchar(64);

ALTER TABLE erp_document_items_archive
    ALTER COLUMN stock_status TYPE varchar(64);

COMMIT;
