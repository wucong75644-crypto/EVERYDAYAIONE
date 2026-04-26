-- 102: refresh_tokens 表 —— 双 Token 无感刷新基础设施
-- 设计：每次 refresh 轮换新 token，旧 token 立即作废（rotation）
-- 只存 SHA-256 哈希，不存明文（泄库不影响安全性）

CREATE TABLE IF NOT EXISTS refresh_tokens (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash  TEXT NOT NULL UNIQUE,          -- SHA-256(refresh_token)
    expires_at  TIMESTAMPTZ NOT NULL,
    revoked     BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at  TIMESTAMPTZ                    -- 吊销时间（rotation / logout / 主动吊销）
);

-- 查询索引：按用户查、按 hash 查
CREATE INDEX IF NOT EXISTS idx_refresh_tokens_user_id ON refresh_tokens(user_id);
CREATE INDEX IF NOT EXISTS idx_refresh_tokens_token_hash ON refresh_tokens(token_hash);

-- 定期清理过期/已吊销 token 的索引
CREATE INDEX IF NOT EXISTS idx_refresh_tokens_cleanup
    ON refresh_tokens(expires_at) WHERE revoked = FALSE;
