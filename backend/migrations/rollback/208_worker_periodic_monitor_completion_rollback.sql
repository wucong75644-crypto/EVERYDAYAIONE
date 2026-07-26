SET LOCAL ROLE everydayai_owner;

REVOKE EXECUTE ON FUNCTION
    worker_claim_periodic_job(TEXT),
    worker_renew_periodic_job(TEXT, UUID),
    worker_finish_periodic_job(TEXT, UUID, BOOLEAN),
    worker_wecom_identity_health_snapshot()
FROM everydayai_worker;

DROP FUNCTION worker_wecom_identity_health_snapshot();
DROP FUNCTION worker_finish_periodic_job(TEXT, UUID, BOOLEAN);
DROP FUNCTION worker_renew_periodic_job(TEXT, UUID);
DROP FUNCTION worker_claim_periodic_job(TEXT);
DROP FUNCTION _assert_global_worker_periodic_scope();
DROP TABLE worker_periodic_job_runs;

-- 模型评分函数保持 208 的向后兼容实现。恢复迁移 198 会重新引入
-- 64 位哈希拒绝和重复审核写入，不属于安全回滚。

RESET ROLE;
