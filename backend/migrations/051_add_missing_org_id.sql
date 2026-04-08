-- 051: 补充缺失的 org_id 列
-- 多租户隔离架构 P3 — 技术方案 §6.1
--
-- 影响表：messages, user_memory_settings, knowledge_edges
-- 执行条件：维护窗口，messages 回填可能耗时（大表分批 UPDATE）
-- 回滚方式：ALTER TABLE xxx DROP COLUMN org_id（但会丢失回填数据）

-- ============================================================
-- 1. messages 表 — 加 org_id 列 + 索引 + 分批回填
-- ============================================================

ALTER TABLE messages ADD COLUMN IF NOT EXISTS org_id UUID REFERENCES organizations(id);

CREATE INDEX IF NOT EXISTS idx_messages_org_id ON messages(org_id)
    WHERE org_id IS NOT NULL;

-- 分批回填：从 conversations 表继承 org_id
-- 每批 1 万条，避免长事务锁表
DO $$
DECLARE batch_count INT;
BEGIN
    LOOP
        WITH batch AS (
            SELECT m.id, c.org_id
            FROM messages m
            JOIN conversations c ON m.conversation_id = c.id
            WHERE m.org_id IS NULL AND c.org_id IS NOT NULL
            LIMIT 10000
        )
        UPDATE messages SET org_id = batch.org_id
        FROM batch WHERE messages.id = batch.id;

        GET DIAGNOSTICS batch_count = ROW_COUNT;
        RAISE NOTICE 'messages backfill batch: % rows', batch_count;
        EXIT WHEN batch_count = 0;
        PERFORM pg_sleep(0.1);
    END LOOP;
END $$;


-- ============================================================
-- 2. user_memory_settings 表 — 加 org_id 列
-- ============================================================

ALTER TABLE user_memory_settings ADD COLUMN IF NOT EXISTS org_id UUID
    REFERENCES organizations(id);

CREATE INDEX IF NOT EXISTS idx_ums_org_id ON user_memory_settings(org_id)
    WHERE org_id IS NOT NULL;


-- ============================================================
-- 3. knowledge_edges 表 — 加 org_id 列 + 回填
-- ============================================================

ALTER TABLE knowledge_edges ADD COLUMN IF NOT EXISTS org_id UUID
    REFERENCES organizations(id);

CREATE INDEX IF NOT EXISTS idx_ke_org_id ON knowledge_edges(org_id)
    WHERE org_id IS NOT NULL;

-- 回填：从 source node 继承 org_id（显式事务，防竞态）
DO $$
BEGIN
    UPDATE knowledge_edges e SET org_id = n.org_id
    FROM knowledge_nodes n
    WHERE e.source_id = n.id AND e.org_id IS NULL AND n.org_id IS NOT NULL;
    RAISE NOTICE 'knowledge_edges backfill: % rows', (SELECT COUNT(*) FROM knowledge_edges WHERE org_id IS NOT NULL);
END $$;
