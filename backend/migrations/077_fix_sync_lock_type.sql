-- 077: 修复 erp_try_acquire_sync_lock 中 v_acquired 类型错误
-- 根因：v_acquired 声明为 BOOLEAN，但 GET DIAGNOSTICS ROW_COUNT 返回 INTEGER
-- 导致 RETURN v_acquired > 0 报错：operator does not exist: boolean > integer
CREATE OR REPLACE FUNCTION erp_try_acquire_sync_lock(
    p_lock_ttl_seconds INT DEFAULT 300,
    p_org_id UUID DEFAULT NULL
)
RETURNS BOOLEAN
LANGUAGE plpgsql
AS $$
DECLARE
    v_acquired INTEGER;
BEGIN
    UPDATE erp_sync_state
    SET status = 'running', last_run_at = NOW()
    WHERE sync_type = 'purchase'
      AND ((p_org_id IS NULL AND org_id IS NULL) OR org_id = p_org_id)
      AND (
          status != 'running'
          OR last_run_at < NOW() - (p_lock_ttl_seconds || ' seconds')::INTERVAL
      );

    GET DIAGNOSTICS v_acquired = ROW_COUNT;
    RETURN v_acquired > 0;
END;
$$;
