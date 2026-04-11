-- 057: erp_document_items + archive 业务时间列升级为 TIMESTAMPTZ
-- 背景: 这 6 列存的是订单/单据业务时间，可能被 BI 工具直连/迁云数据库时按 session TZ 解读
--      统一为 TIMESTAMPTZ 后，PG 内部存 UTC，任何客户端都能正确还原
-- 不动列: synced_at / updated_at 等内部状态时间戳保持 TIMESTAMP，不进 LLM 也不接 BI
--
-- 行为: ALTER COLUMN TYPE 触发全表 REWRITE + 所有相关索引自动重建
--      USING ... AT TIME ZONE 'Asia/Shanghai' 把现有 naive 值视为北京时间转换
--
-- 注意事项:
--   - 需要停 sync worker（即停 backend 服务）防止并发写入等锁
--   - 单事务原子，失败自动回滚不留半吊子状态
--   - lock_timeout 60s 防止永久卡住（被长事务阻塞会快速失败而不是无限等待）
--   - statement_timeout 60min 给重写留足时间（1400 万行 + 14 索引重建预计 20-40 分钟）

BEGIN;

SET LOCAL lock_timeout = '60s';
SET LOCAL statement_timeout = '60min';

-- ────────────────────────────────────────────────────────
-- 热表 erp_document_items：6 列业务时间
-- ────────────────────────────────────────────────────────
ALTER TABLE erp_document_items
    ALTER COLUMN doc_created_at  TYPE TIMESTAMPTZ USING doc_created_at  AT TIME ZONE 'Asia/Shanghai',
    ALTER COLUMN doc_modified_at TYPE TIMESTAMPTZ USING doc_modified_at AT TIME ZONE 'Asia/Shanghai',
    ALTER COLUMN pay_time        TYPE TIMESTAMPTZ USING pay_time        AT TIME ZONE 'Asia/Shanghai',
    ALTER COLUMN consign_time    TYPE TIMESTAMPTZ USING consign_time    AT TIME ZONE 'Asia/Shanghai',
    ALTER COLUMN finished_at     TYPE TIMESTAMPTZ USING finished_at     AT TIME ZONE 'Asia/Shanghai',
    ALTER COLUMN delivery_date   TYPE TIMESTAMPTZ USING delivery_date   AT TIME ZONE 'Asia/Shanghai';

-- ────────────────────────────────────────────────────────
-- 归档表 erp_document_items_archive：同样 6 列
-- ────────────────────────────────────────────────────────
ALTER TABLE erp_document_items_archive
    ALTER COLUMN doc_created_at  TYPE TIMESTAMPTZ USING doc_created_at  AT TIME ZONE 'Asia/Shanghai',
    ALTER COLUMN doc_modified_at TYPE TIMESTAMPTZ USING doc_modified_at AT TIME ZONE 'Asia/Shanghai',
    ALTER COLUMN pay_time        TYPE TIMESTAMPTZ USING pay_time        AT TIME ZONE 'Asia/Shanghai',
    ALTER COLUMN consign_time    TYPE TIMESTAMPTZ USING consign_time    AT TIME ZONE 'Asia/Shanghai',
    ALTER COLUMN finished_at     TYPE TIMESTAMPTZ USING finished_at     AT TIME ZONE 'Asia/Shanghai',
    ALTER COLUMN delivery_date   TYPE TIMESTAMPTZ USING delivery_date   AT TIME ZONE 'Asia/Shanghai';

COMMIT;

-- ────────────────────────────────────────────────────────
-- 验证（手动执行）
-- ────────────────────────────────────────────────────────
-- \d erp_document_items
-- 期望: doc_created_at / doc_modified_at / pay_time / consign_time / finished_at / delivery_date
--       6 列显示为 "timestamp with time zone"
--
-- SELECT doc_created_at, pay_time, consign_time
-- FROM erp_document_items
-- ORDER BY doc_created_at DESC LIMIT 3;
-- 期望: 时间数值不变（迁移前 2026-04-11 15:00 → 迁移后仍然 2026-04-11 15:00 +08）
