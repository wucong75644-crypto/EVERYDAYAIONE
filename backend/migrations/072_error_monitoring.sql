-- ============================================================
-- 072: 全局错误监控 — error_logs 表 + 清理函数
-- 背景：收集所有 logger.error/critical 日志到 DB，
--       管理面板可查 + AI 分析 + 致命级推企微
-- ============================================================

-- 错误日志表
CREATE TABLE IF NOT EXISTS error_logs (
    id              BIGSERIAL PRIMARY KEY,
    fingerprint     VARCHAR(32) NOT NULL,           -- MD5(module:function:error_key) 用于去重聚合
    level           VARCHAR(10) NOT NULL DEFAULT 'ERROR',  -- ERROR / CRITICAL
    module          VARCHAR(200),                   -- 来源模块 (loguru record.name)
    function        VARCHAR(200),                   -- 来源函数 (loguru record.function)
    line            INTEGER,                        -- 来源行号
    message         TEXT NOT NULL,                  -- 错误消息（截断到 2000 字符）
    traceback       TEXT,                           -- 堆栈（截断到 5000 字符）
    occurrence_count INTEGER NOT NULL DEFAULT 1,    -- 同指纹聚合计数
    first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    org_id          UUID,                           -- 可关联企业（从日志上下文提取）
    is_critical     BOOLEAN NOT NULL DEFAULT FALSE, -- 致命级标记（触发企微推送）
    is_resolved     BOOLEAN NOT NULL DEFAULT FALSE, -- 已处理标记
    resolved_at     TIMESTAMPTZ,
    resolved_by     UUID
);

-- 查询索引：按时间倒序 + 未处理优先
CREATE INDEX IF NOT EXISTS idx_error_logs_last_seen
    ON error_logs (last_seen_at DESC);

CREATE INDEX IF NOT EXISTS idx_error_logs_unresolved
    ON error_logs (is_resolved, is_critical, last_seen_at DESC)
    WHERE is_resolved = FALSE;

-- 去重聚合索引：相同指纹快速定位
CREATE UNIQUE INDEX IF NOT EXISTS uq_error_logs_fingerprint
    ON error_logs (fingerprint)
    WHERE is_resolved = FALSE;

-- 30 天清理函数
CREATE OR REPLACE FUNCTION cleanup_old_error_logs(retention_days INTEGER DEFAULT 30)
RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    DELETE FROM error_logs
    WHERE last_seen_at < now() - (retention_days || ' days')::INTERVAL;
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;
