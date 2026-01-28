-- Migration: 009_add_generation_params_constraint.sql
-- Description: 添加 generation_params 大小约束，防止 DoS 攻击
-- Date: 2026-01-28

-- 添加 10KB 大小限制约束
ALTER TABLE messages ADD CONSTRAINT generation_params_size_limit
  CHECK (pg_column_size(generation_params) < 10240);

-- 添加注释
COMMENT ON CONSTRAINT generation_params_size_limit ON messages
  IS '限制 generation_params 大小不超过 10KB，防止存储滥用';
