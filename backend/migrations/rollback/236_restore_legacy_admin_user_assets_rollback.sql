-- 236 rollback 不会重新开放 Runtime 资产门面。
-- 如需恢复 Runtime 版本，应使用对应数据库备份和 Runtime 迁移链。

DO $$
BEGIN
    RAISE EXCEPTION
        '236 rollback requires restoring the pre-232 Runtime tenant contract';
END;
$$;
