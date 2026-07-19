-- 140: Grok 式通用 Session Memory 持久层
-- 依赖 113_memory_v2_schema.sql 与 120_conversation_actor_core.sql。
-- 本迁移只增加可回滚协议；应用接线由后续阶段完成。

CREATE TABLE IF NOT EXISTS memory_session_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL,
    conversation_id UUID NOT NULL
        REFERENCES conversations(id) ON DELETE CASCADE,
    from_revision BIGINT NOT NULL,
    through_revision BIGINT NOT NULL,
    trigger TEXT NOT NULL,
    content JSONB NOT NULL,
    source_refs JSONB NOT NULL DEFAULT '[]'::JSONB,
    content_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT memory_session_logs_revision_check
        CHECK (
            from_revision >= 0
            AND through_revision > from_revision
        ),
    CONSTRAINT memory_session_logs_content_check
        CHECK (
            jsonb_typeof(content) = 'object'
            AND pg_column_size(content) <= 262144
        ),
    CONSTRAINT memory_session_logs_source_refs_check
        CHECK (
            jsonb_typeof(source_refs) = 'array'
            AND pg_column_size(source_refs) <= 262144
        ),
    CONSTRAINT memory_session_logs_hash_check
        CHECK (NULLIF(BTRIM(content_hash), '') IS NOT NULL),
    CONSTRAINT memory_session_logs_status_check
        CHECK (status IN ('ready', 'failed')),
    CONSTRAINT memory_session_logs_model_check
        CHECK (
            NULLIF(BTRIM(model), '') IS NOT NULL
            AND NULLIF(BTRIM(prompt_version), '') IS NOT NULL
        ),
    CONSTRAINT memory_session_logs_flush_unique
        UNIQUE (
            conversation_id,
            from_revision,
            through_revision,
            prompt_version
        )
);

CREATE INDEX IF NOT EXISTS idx_memory_session_logs_user_created
    ON memory_session_logs(user_id, created_at DESC)
    WHERE status = 'ready';
CREATE INDEX IF NOT EXISTS idx_memory_session_logs_conversation_revision
    ON memory_session_logs(conversation_id, through_revision DESC)
    WHERE status = 'ready';

ALTER TABLE memory_atoms
    ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'active',
    ADD COLUMN IF NOT EXISTS source_session_log_id UUID
        REFERENCES memory_session_logs(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS explicitness TEXT,
    ADD COLUMN IF NOT EXISTS valid_from TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS valid_until TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS superseded_by UUID
        REFERENCES memory_atoms(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS confirmed_by_user BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS content_hash TEXT,
    ADD COLUMN IF NOT EXISTS last_recalled_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS recall_count BIGINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS skill_id TEXT,
    ADD COLUMN IF NOT EXISTS skill_version TEXT;

DO $constraints$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'memory_atoms_status_check'
          AND conrelid = 'memory_atoms'::regclass
    ) THEN
        ALTER TABLE memory_atoms
            ADD CONSTRAINT memory_atoms_status_check
            CHECK (
                status IN (
                    'active', 'superseded', 'conflict', 'expired', 'deleted'
                )
            );
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'memory_atoms_explicitness_check'
          AND conrelid = 'memory_atoms'::regclass
    ) THEN
        ALTER TABLE memory_atoms
            ADD CONSTRAINT memory_atoms_explicitness_check
            CHECK (
                explicitness IS NULL
                OR explicitness IN ('explicit', 'confirmed')
            );
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'memory_atoms_valid_range_check'
          AND conrelid = 'memory_atoms'::regclass
    ) THEN
        ALTER TABLE memory_atoms
            ADD CONSTRAINT memory_atoms_valid_range_check
            CHECK (
                valid_from IS NULL
                OR valid_until IS NULL
                OR valid_until >= valid_from
            );
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'memory_atoms_recall_count_check'
          AND conrelid = 'memory_atoms'::regclass
    ) THEN
        ALTER TABLE memory_atoms
            ADD CONSTRAINT memory_atoms_recall_count_check
            CHECK (recall_count >= 0);
    END IF;
END
$constraints$;

CREATE INDEX IF NOT EXISTS idx_memory_atoms_session_log
    ON memory_atoms(source_session_log_id)
    WHERE source_session_log_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_memory_atoms_active_hash
    ON memory_atoms(org_id, user_id, content_hash)
    WHERE status = 'active' AND NOT is_deleted AND content_hash IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_memory_atoms_recall
    ON memory_atoms(user_id, last_recalled_at DESC)
    WHERE status = 'active' AND NOT is_deleted;

ALTER TABLE memory_session_logs ENABLE ROW LEVEL SECURITY;

DO $grant$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE
            ON memory_session_logs TO service_role;
    END IF;
END
$grant$;
