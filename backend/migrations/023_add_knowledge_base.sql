-- Agent 自主知识库数据库迁移
-- 1. knowledge_metrics：结构化任务指标（每次任务必记，零 LLM 成本）
-- 2. knowledge_nodes：知识实体（图节点 + pgvector 向量检索）
-- 3. knowledge_edges：知识关系（图边，递归 CTE 遍历）

-- 确保 pgvector 扩展已启用（add_memory_support.sql 已创建，这里做兼容性检查）
CREATE EXTENSION IF NOT EXISTS vector;

-- ===== 表 1：knowledge_metrics =====

CREATE TABLE IF NOT EXISTS knowledge_metrics (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_type TEXT NOT NULL,                        -- chat / image / video
    model_id TEXT NOT NULL,                         -- 使用的模型
    status TEXT NOT NULL,                           -- success / failed
    error_code TEXT,                                -- 错误码
    cost_time_ms INT,                               -- 耗时（毫秒）
    prompt_tokens INT DEFAULT 0,                    -- 输入 token 数（Chat）
    completion_tokens INT DEFAULT 0,                -- 输出 token 数（Chat）
    prompt_category TEXT,                           -- 提示词分类
    params JSONB DEFAULT '{}',                      -- 任务参数（分辨率、时长等）
    retried BOOLEAN NOT NULL DEFAULT FALSE,         -- 是否经过智能重试
    retry_from_model TEXT,                          -- 重试前的失败模型
    user_id UUID,                                   -- 用户 ID
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_metrics_model_type
    ON knowledge_metrics(model_id, task_type);
CREATE INDEX IF NOT EXISTS idx_metrics_created
    ON knowledge_metrics(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_metrics_status
    ON knowledge_metrics(status);

-- ===== 表 2：knowledge_nodes =====

CREATE TABLE IF NOT EXISTS knowledge_nodes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    category TEXT NOT NULL CHECK (category IN ('model', 'tool', 'experience')),
    subcategory TEXT,                               -- image_gen / chat / video_gen 等
    node_type TEXT NOT NULL,                        -- model / capability / parameter / pattern / error
    title TEXT NOT NULL,                            -- 短标题（≤100 字）
    content TEXT NOT NULL,                          -- 详细知识内容（≤1000 字）
    metadata JSONB DEFAULT '{}',                    -- 灵活元数据
    embedding vector(1024),                         -- DashScope text-embedding-v3
    source TEXT NOT NULL DEFAULT 'auto' CHECK (source IN ('auto', 'seed', 'manual', 'aggregated')),
    confidence FLOAT NOT NULL DEFAULT 0.5,          -- 置信度 0.0-1.0
    hit_count INT NOT NULL DEFAULT 0,               -- 被检索命中次数
    scope TEXT NOT NULL DEFAULT 'global',           -- global / user:{id}
    content_hash TEXT UNIQUE,                       -- 内容哈希（去重）
    is_deleted BOOLEAN NOT NULL DEFAULT FALSE,      -- 软删除
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_nodes_category
    ON knowledge_nodes(category, subcategory);
CREATE INDEX IF NOT EXISTS idx_nodes_scope
    ON knowledge_nodes(scope);
CREATE INDEX IF NOT EXISTS idx_nodes_confidence
    ON knowledge_nodes(confidence DESC);

-- pgvector 向量索引（ivfflat，cosine 距离）
-- 注意：ivfflat 索引需要表中有数据后才能高效工作，
-- lists 参数应 ≈ sqrt(行数)，初始设 10（种子数据 ~20 条），后续数据增长后可 REINDEX
CREATE INDEX IF NOT EXISTS idx_nodes_embedding
    ON knowledge_nodes USING ivfflat (embedding vector_cosine_ops) WITH (lists = 10);

-- ===== 表 3：knowledge_edges =====

CREATE TABLE IF NOT EXISTS knowledge_edges (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id UUID NOT NULL REFERENCES knowledge_nodes(id) ON DELETE CASCADE,
    target_id UUID NOT NULL REFERENCES knowledge_nodes(id) ON DELETE CASCADE,
    relation_type TEXT NOT NULL,                    -- good_at / struggles_with / better_than / requires / produces / related_to
    weight FLOAT NOT NULL DEFAULT 1.0,              -- 关系权重
    metadata JSONB DEFAULT '{}',                    -- 关系元数据
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_edges_source
    ON knowledge_edges(source_id);
CREATE INDEX IF NOT EXISTS idx_edges_target
    ON knowledge_edges(target_id);
CREATE INDEX IF NOT EXISTS idx_edges_type
    ON knowledge_edges(relation_type);

-- 防止重复边
CREATE UNIQUE INDEX IF NOT EXISTS idx_edges_unique
    ON knowledge_edges(source_id, target_id, relation_type);
