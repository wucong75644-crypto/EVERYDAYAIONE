-- ========================================
-- Rollback: 005_add_video_cost_enum
-- Description: 回滚 video_generation_cost 枚举值
-- 注意：PostgreSQL 不支持直接删除枚举值
-- 需要重建枚举类型或忽略此回滚
-- ========================================

-- PostgreSQL 不支持 DROP VALUE，此回滚需要手动处理：
-- 1. 创建新的枚举类型（不含 video_generation_cost）
-- 2. 迁移数据
-- 3. 删除旧枚举，重命名新枚举

-- 如果不需要完全回滚，可以选择保留枚举值（向后兼容）
-- 以下代码仅供参考，生产环境请谨慎执行

/*
-- 步骤1：创建临时枚举（不含 video_generation_cost）
CREATE TYPE credits_change_type_new AS ENUM (
    'register_gift',
    'daily_reward',
    'purchase',
    'chat_cost',
    'image_generation_cost',
    'admin_adjustment',
    'refund'
);

-- 步骤2：更新使用该枚举的表（先删除使用 video_generation_cost 的记录或转换）
UPDATE credits_history
SET change_type = 'image_generation_cost'
WHERE change_type = 'video_generation_cost';

-- 步骤3：修改列类型
ALTER TABLE credits_history
ALTER COLUMN change_type TYPE credits_change_type_new
USING change_type::text::credits_change_type_new;

-- 步骤4：删除旧枚举，重命名新枚举
DROP TYPE credits_change_type;
ALTER TYPE credits_change_type_new RENAME TO credits_change_type;
*/

SELECT 'Warning: Enum value removal requires manual intervention. See comments in this file.' AS message;
