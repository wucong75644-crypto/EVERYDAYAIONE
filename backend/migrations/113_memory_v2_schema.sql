-- 记忆系统 V2：四层架构（L0→L1→L2→L3）
-- 参考：docs/document/TECH_记忆系统四层架构重构.md
-- 依赖：021_add_memory_support.sql（pgvector 扩展 + user_memory_settings 表）

-- ============================================================
-- L1: memory_atoms — 原子事实记忆
-- ============================================================
CREATE TABLE IF NOT EXISTS memory_atoms (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL,
    user_id UUID NOT NULL,

    -- 内容
    content TEXT NOT NULL,
    type VARCHAR(20) NOT NULL CHECK (type IN ('persona', 'episodic', 'instruction')),
    priority INTEGER NOT NULL DEFAULT 50,
    scene_name VARCHAR(200) DEFAULT '',

    -- 溯源
    source_message_ids UUID[] DEFAULT '{}',
    session_id UUID,

    -- 时间语义（episodic 专用）
    activity_start_time TIMESTAMPTZ,
    activity_end_time TIMESTAMPTZ,
    merge_timestamps TIMESTAMPTZ[] DEFAULT '{}',

    -- 向量（text-embedding-v3, 1024维）
    embedding vector(1024),

    -- 全文搜索（应用层 jieba 分词后写入）
    content_tsv tsvector,

    -- 元数据
    metadata JSONB DEFAULT '{}',
    is_deleted BOOLEAN DEFAULT FALSE,

    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 业务查询索引
CREATE INDEX idx_atoms_org_user ON memory_atoms(org_id, user_id) WHERE NOT is_deleted;
CREATE INDEX idx_atoms_user_updated ON memory_atoms(user_id, updated_at DESC) WHERE NOT is_deleted;
CREATE INDEX idx_atoms_type ON memory_atoms(type) WHERE NOT is_deleted;
CREATE INDEX idx_atoms_scene ON memory_atoms(scene_name) WHERE scene_name != '' AND NOT is_deleted;
CREATE INDEX idx_atoms_session ON memory_atoms(session_id) WHERE session_id IS NOT NULL;
CREATE INDEX idx_atoms_priority ON memory_atoms(priority DESC) WHERE NOT is_deleted;

-- 向量检索索引（ivfflat，cosine 距离）
-- 注：数据量 < 1000 时 ivfflat 退化为全量扫描，lists 设小一点
CREATE INDEX idx_atoms_embedding ON memory_atoms
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 50);

-- 全文搜索索引（GIN）
CREATE INDEX idx_atoms_tsv ON memory_atoms USING gin (content_tsv);


-- ============================================================
-- L2: memory_scenes — 语义场景
-- ============================================================
CREATE TABLE IF NOT EXISTS memory_scenes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL,
    user_id UUID NOT NULL,

    -- 内容
    title VARCHAR(200) NOT NULL,
    summary VARCHAR(500) NOT NULL,
    content TEXT NOT NULL,

    -- 管理
    heat INTEGER DEFAULT 1,
    status VARCHAR(20) DEFAULT 'active' CHECK (status IN ('active', 'archived')),

    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_scenes_org_user ON memory_scenes(org_id, user_id) WHERE status = 'active';
CREATE INDEX idx_scenes_heat ON memory_scenes(user_id, heat DESC) WHERE status = 'active';


-- ============================================================
-- L3: memory_personas — 用户画像
-- ============================================================
CREATE TABLE IF NOT EXISTS memory_personas (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL,
    user_id UUID NOT NULL,

    -- 内容
    content TEXT NOT NULL,
    archetype VARCHAR(300),

    -- 版本
    version INTEGER DEFAULT 1,
    trigger_reason VARCHAR(500),

    -- 统计
    total_atoms_processed INTEGER DEFAULT 0,
    total_scenes INTEGER DEFAULT 0,

    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    -- 每用户每组织唯一
    UNIQUE(org_id, user_id)
);


-- ============================================================
-- 管道状态：memory_pipeline_state
-- ============================================================
CREATE TABLE IF NOT EXISTS memory_pipeline_state (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL,
    user_id UUID NOT NULL,
    session_id UUID,

    -- L1 状态
    conversation_count INTEGER DEFAULT 0,
    warmup_threshold INTEGER DEFAULT 1,
    last_l1_at TIMESTAMPTZ,
    l1_cursor_timestamp TIMESTAMPTZ,
    last_scene_name VARCHAR(200),

    -- L2 状态
    last_l2_at TIMESTAMPTZ,
    l2_fire_time TIMESTAMPTZ,

    -- L3 状态
    atoms_since_last_persona INTEGER DEFAULT 0,
    last_persona_at TIMESTAMPTZ,
    request_persona_update BOOLEAN DEFAULT FALSE,
    persona_update_reason VARCHAR(500),

    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE(org_id, user_id, session_id)
);

CREATE INDEX idx_pipeline_user_session ON memory_pipeline_state(user_id, session_id);


-- ============================================================
-- 扩展 user_memory_settings
-- ============================================================
ALTER TABLE user_memory_settings
    ADD COLUMN IF NOT EXISTS max_scenes INTEGER DEFAULT 15,
    ADD COLUMN IF NOT EXISTS l1_trigger_every_n INTEGER DEFAULT 5,
    ADD COLUMN IF NOT EXISTS persona_trigger_every_n INTEGER DEFAULT 50,
    ADD COLUMN IF NOT EXISTS memory_version VARCHAR(10) DEFAULT 'v2';
