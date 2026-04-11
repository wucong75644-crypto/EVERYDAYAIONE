-- 071: 定时任务扩展 — 单次任务 + 结构化频率
--
-- 背景：之前所有任务都用 cron_expr 表示，单次任务无法表达，
-- 用户也不友好（要写 "0 22 11 4 *" 这种）。
-- 现在加 schedule_type 字段把"频率类型"语义化：
--
-- - once    一次性任务，跑完自动暂停。next_run_at = run_at
-- - daily   每天 HH:MM
-- - weekly  每周指定日 HH:MM（weekdays SMALLINT[]，cron dow 语义 0=日 1=一 ... 6=六）
-- - monthly 每月几号 HH:MM（day_of_month SMALLINT 1-31）
-- - cron    自定义 cron 表达式（兼容旧数据 + 高级用户）
--
-- cron_expr 仍然作为底层存储：daily/weekly/monthly 都会被组装成对应的 cron
-- 写入 cron_expr 字段，调度器只需要看 next_run_at 不需要关心 schedule_type。
-- once 任务的 cron_expr 为 NULL（因为没有重复语义）。
--
-- 兼容性：旧数据（schedule_type 默认 'cron'）保留 cron_expr，行为不变。

ALTER TABLE scheduled_tasks
    ADD COLUMN IF NOT EXISTS schedule_type VARCHAR(20) NOT NULL DEFAULT 'cron'
        CHECK (schedule_type IN ('once', 'daily', 'weekly', 'monthly', 'cron')),
    ADD COLUMN IF NOT EXISTS run_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS weekdays SMALLINT[],
    ADD COLUMN IF NOT EXISTS day_of_month SMALLINT
        CHECK (day_of_month IS NULL OR (day_of_month BETWEEN 1 AND 31));

-- cron_expr 改为可空（once 任务时为 NULL）
ALTER TABLE scheduled_tasks ALTER COLUMN cron_expr DROP NOT NULL;

COMMENT ON COLUMN scheduled_tasks.schedule_type IS 'once/daily/weekly/monthly/cron';
COMMENT ON COLUMN scheduled_tasks.run_at IS '单次任务的执行时刻（schedule_type=once 时使用）';
COMMENT ON COLUMN scheduled_tasks.weekdays IS '每周任务的星期数组（cron dow 语义 0=日 1=一 ... 6=六）';
COMMENT ON COLUMN scheduled_tasks.day_of_month IS '每月任务的日期（1-31）';
