-- 多图批次支持：tasks 表新增字段
-- 用途：支持一次图片生成请求产生 N 个并行任务（1/2/3/4 张）
--
-- image_index: 图片在网格中的位置 (0,1,2,3)，NULL = 非图片任务
-- batch_id:    同一批次的所有 task 共享的 UUID，NULL = 非图片任务
-- result_data: 单个 task 的生成结果（JSONB），用于批次最终合并

ALTER TABLE tasks ADD COLUMN IF NOT EXISTS image_index INTEGER DEFAULT NULL;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS batch_id TEXT DEFAULT NULL;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS result_data JSONB DEFAULT NULL;

-- 索引：按 batch_id 查询批次内所有 task（部分索引，仅图片任务命中）
CREATE INDEX IF NOT EXISTS idx_tasks_batch_id ON tasks(batch_id) WHERE batch_id IS NOT NULL;
