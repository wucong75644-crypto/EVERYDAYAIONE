-- 144 rollback: 停止手动 Curated Memory 写入，保留新增字段和数据。

DROP FUNCTION IF EXISTS create_manual_memory(
    UUID, UUID, TEXT, TEXT, TEXT, INTEGER
);
DROP FUNCTION IF EXISTS update_manual_memory(
    UUID, UUID, UUID, TEXT, TEXT, TEXT
);
DROP FUNCTION IF EXISTS delete_memory_atom(UUID, UUID, UUID);
DROP FUNCTION IF EXISTS clear_memory_atoms(UUID, UUID);

DROP INDEX IF EXISTS idx_memory_atoms_personal_active;
DROP INDEX IF EXISTS idx_memory_atoms_org_active;

-- 不恢复 org_id NOT NULL：个人 scope 数据使用 NULL。
-- 不删除 source_kind 或其约束：回滚必须保留已写入事实的来源语义。
