-- 记忆功能数据库迁移
-- 1. 启用 pgvector 扩展（Mem0 向量存储依赖）
-- 2. 创建 user_memory_settings 表（记忆功能开关和配置）

-- 启用 pgvector 扩展
CREATE EXTENSION IF NOT EXISTS vector;

-- 用户记忆设置表
CREATE TABLE IF NOT EXISTS user_memory_settings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    memory_enabled BOOLEAN NOT NULL DEFAULT true,
    retention_days INTEGER NOT NULL DEFAULT 7,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_user_memory_settings_user_id
    ON user_memory_settings(user_id);
