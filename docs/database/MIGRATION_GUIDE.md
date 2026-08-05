# 数据库迁移指南

## 生产 Schema 政策

- 生产 Schema 只允许通过 `deploy/run-migrations.sh` 的 `plan` / `apply` 路径向前迁移。生产环境禁止执行仓库中的 rollback SQL，也不提供 rollback runner。
- `schema_migration_ledger` 始终记录 forward migration 权威历史。不得删除、倒退或建立第二套 ledger 状态来表示 Schema 回退。
- `backend/migrations/rollback/` 仅用于 disposable PostgreSQL 的逐 migration、guarded 合同验证；不得批量执行。guard 未通过时必须失败关闭。
- rollback 文件按用途分类：共享数据库能力使用 no-op（例如 `227_01_z`，`pgcrypto` 生命周期归数据库平台/DBA）；其余 guarded 或历史 destructive rollback 均只属于 disposable 验证资产，不是生产恢复手段。
- 每次迁移后的 Schema 必须继续兼容 N-1 二进制至少一个完整发布周期。生产默认不接受“零事实窗口”直接回退 Schema 的例外。

生产迁移后发生故障时，按以下顺序恢复：

1. 关闭相关 Runtime flags，并停止产生新事实。
2. 回退到与当前 Schema 兼容的 N-1 二进制。
3. 完成服务健康检查和关键读写验证；不得删除或倒退 migration ledger。
4. 新建 forward migration 修复 Schema，再按正常 `plan` / `apply` 流程发布。

以下迁移 003 内容是历史记录，其中手工 SQL 与 rollback 示例只可用于 disposable 验证环境，不构成当前生产操作流程。

## 迁移 003：修改 model_id 字段类型 + 添加 last_message_preview 字段

### 问题
1. `conversations` 表的 `model_id` 字段当前为 `UUID` 类型，但代码中使用字符串模型ID（如 `'gemini-3-pro'`），导致保存失败
2. `conversations` 表缺少 `last_message_preview` 字段，导致获取对话列表时后端返回 **500错误**

### 解决方案
1. 将 `model_id` 字段从 `UUID` 改为 `VARCHAR(100)`
2. 添加 `last_message_preview` 字段（TEXT类型）用于存储对话最后一条消息的预览

### 执行步骤

#### 方法1：Supabase Dashboard（推荐）

1. 登录 Supabase Dashboard: https://supabase.com
2. 选择你的项目
3. 点击左侧菜单 **SQL Editor**
4. 点击 **New query**
5. 复制粘贴以下 SQL：

```sql
-- 删除外键约束
ALTER TABLE conversations
DROP CONSTRAINT IF EXISTS conversations_model_id_fkey;

-- 删除索引
DROP INDEX IF EXISTS idx_conversations_model_id;

-- 修改字段类型
ALTER TABLE conversations
ALTER COLUMN model_id TYPE VARCHAR(100) USING model_id::text;

-- 重新创建索引
CREATE INDEX IF NOT EXISTS idx_conversations_model_id ON conversations(model_id);

-- 添加注释
COMMENT ON COLUMN conversations.model_id IS '模型标识符（字符串，如 gemini-3-pro）';

-- 添加 last_message_preview 字段
ALTER TABLE conversations
ADD COLUMN IF NOT EXISTS last_message_preview TEXT;
```

6. 点击 **Run** 执行
7. 确认执行成功（应该显示 "Success. No rows returned"）

#### 方法2：命令行（需要 psql）

如果你有数据库的直接访问权限：

```bash
psql "your-connection-string" -f docs/database/migrations/003_change_model_id_to_varchar.sql
```

### 验证

执行完成后，可以运行以下SQL验证字段已正确添加和修改：

```sql
SELECT column_name, data_type, character_maximum_length
FROM information_schema.columns
WHERE table_name = 'conversations'
  AND column_name IN ('model_id', 'last_message_preview')
ORDER BY column_name;
```

应该返回两行：
- `model_id`: `data_type` = `character varying`, `character_maximum_length` = `100`
- `last_message_preview`: `data_type` = `text`, `character_maximum_length` = `null`（TEXT类型没有长度限制）

### 历史 rollback 示例（仅 disposable 验证）

生产环境禁止执行。仅在隔离 disposable 数据库验证该历史 migration 时使用：

```sql
ALTER TABLE conversations
ALTER COLUMN model_id TYPE UUID USING model_id::uuid;

ALTER TABLE conversations
ADD CONSTRAINT conversations_model_id_fkey
FOREIGN KEY (model_id) REFERENCES models(id) ON DELETE SET NULL;
```

## 迁移历史

- `001_add_image_url_to_messages.sql` - 添加图片URL字段到messages表
- `002_add_video_url_to_messages.sql` - 添加视频URL字段到messages表
- `003_change_model_id_to_varchar.sql` - 修改model_id字段类型 + 添加last_message_preview字段到conversations表
- `004_add_is_error_to_messages.sql` - 添加is_error字段到messages表
