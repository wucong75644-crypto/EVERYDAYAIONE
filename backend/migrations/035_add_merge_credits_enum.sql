-- 035: 为 credits_change_type 枚举添加 'merge' 值
-- 用于账号合并时的积分迁移记录
ALTER TYPE credits_change_type ADD VALUE IF NOT EXISTS 'merge';
