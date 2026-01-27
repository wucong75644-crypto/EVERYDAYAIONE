-- 005_add_video_cost_enum.sql
-- 添加视频生成积分类型枚举值（P0 阻塞问题）
-- 创建日期：2026-01-26

-- 添加枚举值（如果不存在）
DO $$
BEGIN
    -- 检查枚举值是否已存在
    IF NOT EXISTS (
        SELECT 1 FROM pg_enum
        WHERE enumtypid = 'credits_change_type'::regtype
        AND enumlabel = 'video_generation_cost'
    ) THEN
        ALTER TYPE credits_change_type ADD VALUE 'video_generation_cost';
    END IF;
END $$;

-- 验证枚举值已添加
SELECT enumlabel
FROM pg_enum
WHERE enumtypid = 'credits_change_type'::regtype
ORDER BY enumsortorder;
