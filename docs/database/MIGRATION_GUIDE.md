# 数据库迁移指南

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

### 回滚（如需要）

如果需要回滚到 UUID 类型（不推荐）：

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
