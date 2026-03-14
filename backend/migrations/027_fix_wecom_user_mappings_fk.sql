-- 027: 修复 wecom_user_mappings 外键引用
-- 问题：user_id FK 错误引用 auth.users(id)，应引用 public.users(id)
-- 原因：代码在 public.users 创建企微用户，但 FK 检查 auth.users 导致约束冲突

ALTER TABLE wecom_user_mappings
    DROP CONSTRAINT wecom_user_mappings_user_id_fkey;

ALTER TABLE wecom_user_mappings
    ADD CONSTRAINT wecom_user_mappings_user_id_fkey
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;

-- 补充缺失的 increment_message_count 函数（wecom_message_service 需要）
CREATE OR REPLACE FUNCTION public.increment_message_count(conv_id UUID)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
BEGIN
    UPDATE conversations
    SET message_count = message_count + 1,
        updated_at = NOW()
    WHERE id = conv_id;
END;
$$;
