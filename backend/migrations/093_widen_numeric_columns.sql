-- 093: 加宽 erp_document_items 中偏窄的 numeric 列
--
-- 问题根因：
--   1. volume numeric(10,4) 最大 999999.9999，快麦 API 返回的体积可能超过 10^6
--   2. item_discount_rate numeric(5,4) 最大 9.9999，快麦 API 返回的折扣率可能 >= 10
--
-- 变更策略：
--   - volume: numeric(10,4) → numeric(14,4)，支持到 9999999999.9999
--   - item_discount_rate: numeric(5,4) → numeric(10,4)，支持到 999999.9999
--   - 同步变更 archive 表
--
-- PostgreSQL ALTER TYPE numeric 只改元数据，不重写表，瞬间完成。

BEGIN;

ALTER TABLE erp_document_items
    ALTER COLUMN volume TYPE numeric(14,4),
    ALTER COLUMN item_discount_rate TYPE numeric(10,4);

ALTER TABLE erp_document_items_archive
    ALTER COLUMN volume TYPE numeric(14,4),
    ALTER COLUMN item_discount_rate TYPE numeric(10,4);

COMMIT;
