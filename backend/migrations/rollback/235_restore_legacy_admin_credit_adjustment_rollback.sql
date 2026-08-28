-- 235 rollback：恢复到 Runtime 平台管理员 RPC 需要完整的 Runtime tenant 契约。
--
-- 232 已删除该契约，不能在这里伪造 tenant_* 函数或重新开放 Runtime 角色。
-- 如需回到 Runtime 版本，应恢复 232 前的数据库备份并重新执行对应的
-- platform-admin migration；否则不要执行此回滚文件。

DO $$
BEGIN
    RAISE EXCEPTION
        '235 rollback requires restoring the pre-232 Runtime tenant contract';
END;
$$;
