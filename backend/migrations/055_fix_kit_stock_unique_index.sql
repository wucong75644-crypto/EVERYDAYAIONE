-- 055: 修复 mv_kit_stock 唯一索引（去掉 COALESCE 表达式）
--
-- 根因：REFRESH MATERIALIZED VIEW CONCURRENTLY 要求唯一索引直接引用列名，
--       不支持表达式索引（如 COALESCE）。053 迁移创建的索引导致并发刷新永远失败。
-- 修复：重建索引，直接使用 (org_id, outer_id, sku_outer_id)。
--       org_id 在视图数据中不存在 NULL（已验证），安全。

DROP INDEX IF EXISTS uq_mv_kit_stock;

CREATE UNIQUE INDEX uq_mv_kit_stock
    ON mv_kit_stock (org_id, outer_id, sku_outer_id);
