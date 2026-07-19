-- 142: Grok Dream 式通用记忆 Consolidation 持久协议。
-- 依赖 140_generic_memory_session_runtime.sql。
-- 本迁移仅建立 additive run/lineage 状态；模型与调度接线后续完成。

CREATE TABLE IF NOT EXISTS memory_consolidation_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL,
    source_log_ids UUID[] NOT NULL,
    source_hash TEXT NOT NULL,
    input_count INTEGER NOT NULL,
    output_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    receipt JSONB NOT NULL DEFAULT '{}'::JSONB,
    error_code TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    CONSTRAINT memory_consolidation_runs_sources_check
        CHECK (
            cardinality(source_log_ids) >= 3
            AND cardinality(source_log_ids) <= 25
        ),
    CONSTRAINT memory_consolidation_runs_hash_check
        CHECK (NULLIF(BTRIM(source_hash), '') IS NOT NULL),
    CONSTRAINT memory_consolidation_runs_count_check
        CHECK (
            input_count >= 0
            AND output_count >= 0
            AND output_count <= input_count
        ),
    CONSTRAINT memory_consolidation_runs_status_check
        CHECK (status IN ('completed', 'failed')),
    CONSTRAINT memory_consolidation_runs_model_check
        CHECK (
            NULLIF(BTRIM(model), '') IS NOT NULL
            AND NULLIF(BTRIM(prompt_version), '') IS NOT NULL
        ),
    CONSTRAINT memory_consolidation_runs_receipt_check
        CHECK (
            jsonb_typeof(receipt) = 'object'
            AND pg_column_size(receipt) <= 262144
        ),
    CONSTRAINT memory_consolidation_runs_completion_check
        CHECK (
            (status = 'completed' AND completed_at IS NOT NULL)
            OR
            (status = 'failed' AND error_code IS NOT NULL)
        ),
    CONSTRAINT memory_consolidation_runs_source_unique
        UNIQUE (user_id, source_hash)
);

CREATE INDEX IF NOT EXISTS idx_memory_consolidation_runs_user_completed
    ON memory_consolidation_runs(user_id, completed_at DESC)
    WHERE status = 'completed';

ALTER TABLE memory_session_logs
    ADD COLUMN IF NOT EXISTS consolidation_run_id UUID
        REFERENCES memory_consolidation_runs(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS consolidated_at TIMESTAMPTZ;

ALTER TABLE memory_session_logs
    DROP CONSTRAINT IF EXISTS memory_session_logs_status_check;
ALTER TABLE memory_session_logs
    ADD CONSTRAINT memory_session_logs_status_check
    CHECK (status IN ('ready', 'failed', 'consolidated'));

DO $constraints$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'memory_session_logs_consolidation_check'
          AND conrelid = 'memory_session_logs'::regclass
    ) THEN
        ALTER TABLE memory_session_logs
            ADD CONSTRAINT memory_session_logs_consolidation_check
            CHECK (
                (
                    status = 'consolidated'
                    AND consolidation_run_id IS NOT NULL
                    AND consolidated_at IS NOT NULL
                )
                OR
                (
                    status <> 'consolidated'
                    AND consolidation_run_id IS NULL
                    AND consolidated_at IS NULL
                )
            );
    END IF;
END
$constraints$;

CREATE INDEX IF NOT EXISTS idx_memory_session_logs_ready_user
    ON memory_session_logs(user_id, created_at ASC)
    WHERE status = 'ready';
CREATE INDEX IF NOT EXISTS idx_memory_session_logs_consolidation
    ON memory_session_logs(consolidation_run_id)
    WHERE consolidation_run_id IS NOT NULL;

ALTER TABLE memory_consolidation_runs ENABLE ROW LEVEL SECURITY;

DO $grant$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE
            ON memory_consolidation_runs TO service_role;
    END IF;
END
$grant$;
