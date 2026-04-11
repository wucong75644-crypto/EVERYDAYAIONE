-- ============================================================
-- 059: erp_product_skus 加 platform_map_checked_at 列
-- ============================================================
--
-- 背景（Bug 1）：
-- sync_platform_map 原先用 .limit(10000) 取 SKU 列表，导致
-- 45,185 个 SKU 中的 35,185 个（78%）从 2026-03-23 起从未被同步过。
--
-- 修复方案：
-- 加一列 platform_map_checked_at 记录"上次调用 erp.item.outerid.list.get
-- 确认此 SKU 平台映射状态的时间"。sync_platform_map 改用：
--
--   ORDER BY platform_map_checked_at ASC NULLS FIRST LIMIT (total/4)
--
-- 配合 6 小时一轮的调度周期 → 24 小时全量覆盖 → SKU 上架新平台
-- 的感知延迟最长 24 小时。
--
-- 写入时机：
--   1) sync_platform_map 批次成功返回（含空响应）→ 整批标记 now()
--   2) 批次收到 20150（整批 SKU 无映射，业务正常）→ 整批标记 now()
-- 不写入的场景（保留 NULL/旧值，下轮自动重试）：
--   - KuaiMaiTokenExpiredError / KuaiMaiSignatureError
--   - 网络异常、code=1 payload too large（半批降级）
--   - 未知 KuaiMaiBusinessError（写入死信队列异步重试）
--
-- 索引说明（关键 — 必须匹配查询表达式）：
-- 查询用 ORDER BY COALESCE(platform_map_checked_at, '1970-01-01'::timestamp)
-- 因为 LocalDB 的 .order() 不支持 NULLS FIRST 子句，只能用 COALESCE 把 NULL 当作最旧。
-- PG 的索引必须精确匹配查询表达式才能用上，所以索引也用同样的 COALESCE。
-- 双括号 (()) 是 PG 表达式索引语法。
-- EXPLAIN 验证：Index Scan using idx_skus_platform_map_checked → cost ~1170
--               (vs 全表扫描 + Sort cost ~5000)
--
-- 时区说明：
-- 用 TIMESTAMP（无时区）而非 TIMESTAMPTZ，与同表 synced_at 一致。
-- 项目约定（见 057 迁移注释）：内部状态时间戳保持 TIMESTAMP，
-- 不进 LLM 也不接 BI。本列只用于内部增量同步排序，符合该约定。
--
-- 回滚：
--   ALTER TABLE erp_product_skus DROP COLUMN IF EXISTS platform_map_checked_at;
--   DROP INDEX IF EXISTS idx_skus_platform_map_checked;
-- ============================================================

ALTER TABLE erp_product_skus
  ADD COLUMN IF NOT EXISTS platform_map_checked_at TIMESTAMP;

COMMENT ON COLUMN erp_product_skus.platform_map_checked_at IS
  'sync_platform_map 上次确认此 SKU 平台映射状态的时间。'
  'NULL = 未检查，配合 ORDER BY COALESCE(...) 表达式索引实现增量同步。';

CREATE INDEX IF NOT EXISTS idx_skus_platform_map_checked
  ON erp_product_skus
  ((COALESCE(platform_map_checked_at, '1970-01-01'::timestamp)));
