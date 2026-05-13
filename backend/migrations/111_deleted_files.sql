-- 删除文件记录表：confirm_delete 删除 NAS 文件后记录，30 天后定时清理 OSS
CREATE TABLE IF NOT EXISTS deleted_files (
    id BIGSERIAL PRIMARY KEY,
    org_id UUID NOT NULL,
    user_id UUID NOT NULL,
    relative_path TEXT NOT NULL,
    oss_object_key TEXT NOT NULL,
    deleted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    purge_after TIMESTAMPTZ NOT NULL,
    purged BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_deleted_files_purge
ON deleted_files(purge_after) WHERE NOT purged;

COMMENT ON TABLE deleted_files IS '文件删除记录，OSS 延迟 30 天清理';
COMMENT ON COLUMN deleted_files.relative_path IS '相对于 workspace root 的路径';
COMMENT ON COLUMN deleted_files.oss_object_key IS 'OSS 对象键，如 workspace/org/user/下载/a.xlsx';
COMMENT ON COLUMN deleted_files.purge_after IS 'OSS 清理时间 = deleted_at + 30 days';
COMMENT ON COLUMN deleted_files.purged IS 'OSS 是否已清理';
