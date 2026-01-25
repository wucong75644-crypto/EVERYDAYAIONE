# 数据库使用指南

## 一、快速开始

### 1. Supabase 数据库配置

本项目使用 **Supabase PostgreSQL** 作为数据库。

```bash
# 1. 登录 Supabase Dashboard: https://supabase.com/dashboard
# 2. 创建新项目或选择已有项目
# 3. 在 SQL Editor 中执行初始化脚本
# 4. 复制连接字符串到 .env 文件
```

**环境变量配置**:
```bash
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your-anon-key
SUPABASE_SERVICE_KEY=your-service-key
DATABASE_URL=postgresql://postgres:[password]@db.your-project.supabase.co:5432/postgres
```

### 2. 默认超级管理员账号

```
账号: admin
密码: admin123
角色: super_admin
```

⚠️ **安全警告**: 生产环境部署后，请立即修改默认密码！

```sql
-- 修改超级管理员密码的SQL（需要先生成新密码的bcrypt hash）
UPDATE users
SET password_hash = '新的bcrypt_hash'
WHERE phone = 'admin' AND role = 'super_admin';
```

---

## 二、角色权限说明

### 1. 角色定义

| 角色 | 代码值 | 说明 |
|-----|--------|------|
| 普通用户 | `user` | 普通用户，只能访问前台功能 |
| 管理员 | `admin` | 可访问管理后台，管理模型、用户等 |
| 超级管理员 | `super_admin` | 拥有所有权限，可查看用户所有行为和内容 |

### 2. 权限对比表

| 功能 | user | admin | super_admin |
|-----|------|-------|-------------|
| 使用聊天功能 | ✅ | ✅ | ✅ |
| 访问模型广场 | ✅ | ✅ | ✅ |
| 访问管理后台 | ❌ | ✅ | ✅ |
| 模型开放/关闭 | ❌ | ✅ | ✅ |
| 查看用户列表 | ❌ | ✅ | ✅ |
| 调整用户积分 | ❌ | ✅ | ✅ |
| 禁用/启用用户 | ❌ | ✅ | ✅ |
| **查看用户对话内容** | ❌ | ❌ | ✅ |
| **查看用户生成图片** | ❌ | ❌ | ✅ |
| **查看所有操作日志** | ❌ | ❌ | ✅ |
| **设置管理员权限** | ❌ | ❌ | ✅ |

### 3. 超级管理员专属权限详解

超级管理员可以查看用户的**所有行为和内容**，包括：

#### 3.1 用户对话记录
- 查看任意用户的所有对话历史
- 查看对话的完整内容（用户提问 + AI回复）
- 查看对话使用的模型、时间、积分消耗等详细信息

#### 3.2 用户图片生成记录
- 查看任意用户生成的所有图片
- 查看图片的生成提示词（prompt）
- 查看图片的参数、模型、积分消耗等信息

#### 3.3 用户行为追踪
- 用户登录时间、IP地址
- 用户订阅的模型
- 用户积分变动历史
- 用户总消耗统计

#### 3.4 操作审计
- 查看所有管理员的操作日志
- 查看谁在什么时候查看了哪个用户的隐私数据
- 操作原因记录

---

## 三、数据表说明

### 1. 核心业务表

| 表名 | 说明 | 关键字段 |
|-----|------|---------|
| `users` | 用户表 | id, nickname, phone, role, credits |
| `models` | 模型表 | id, name, status, is_default |
| `conversations` | 对话记录表 | id, user_id, model_id, title |
| `messages` | 消息记录表 | id, conversation_id, role, content |
| `image_generations` | 图片生成记录表 | id, user_id, prompt, image_url |

### 2. 关联表

| 表名 | 说明 |
|-----|------|
| `user_subscriptions` | 用户订阅模型关联表 |

### 3. 日志表

| 表名 | 说明 |
|-----|------|
| `credits_history` | 积分变动历史 |
| `admin_action_logs` | 管理员操作日志 |

### 4. 视图

| 视图名 | 说明 |
|-------|------|
| `v_user_stats` | 用户统计视图（订阅模型数、对话数、积分消耗） |
| `v_model_stats` | 模型统计视图（订阅人数、使用次数） |

---

## 四、常用查询示例

### 1. 用户管理

```sql
-- 查询所有用户统计信息
SELECT * FROM v_user_stats ORDER BY created_at DESC;

-- 查询某个用户的详细信息
SELECT * FROM users WHERE id = 123;

-- 查询某个用户的所有对话
SELECT * FROM conversations WHERE user_id = 123 ORDER BY updated_at DESC;

-- 查询某个对话的所有消息
SELECT * FROM messages WHERE conversation_id = 456 ORDER BY created_at ASC;

-- 查询某个用户生成的所有图片
SELECT * FROM image_generations WHERE user_id = 123 ORDER BY created_at DESC;

-- 查询某个用户的积分历史
SELECT * FROM credits_history WHERE user_id = 123 ORDER BY created_at DESC;
```

### 2. 模型管理

```sql
-- 查询所有模型及统计信息
SELECT * FROM v_model_stats;

-- 查询开放中的模型
SELECT * FROM models WHERE status = 'active';

-- 查询默认模型
SELECT * FROM models WHERE is_default = TRUE;

-- 关闭某个模型
UPDATE models SET status = 'maintenance' WHERE id = 5;

-- 开放某个模型
UPDATE models SET status = 'active' WHERE id = 5;
```

### 3. 权限管理

```sql
-- 查询所有管理员
SELECT * FROM users WHERE role IN ('admin', 'super_admin');

-- 设置用户为管理员
UPDATE users SET role = 'admin' WHERE id = 123;

-- 设置用户为超级管理员
UPDATE users SET role = 'super_admin' WHERE id = 123;

-- 取消管理员权限
UPDATE users SET role = 'user' WHERE id = 123;
```

### 4. 积分管理

```sql
-- 给用户充值积分
UPDATE users SET credits = credits + 100 WHERE id = 123;

-- 记录积分变动到历史表
INSERT INTO credits_history (user_id, change_amount, balance_after, change_type, description, operator_id)
SELECT 123, 100, credits, 'admin_adjust', '管理员充值', 1
FROM users WHERE id = 123;

-- 查询积分不足的用户
SELECT * FROM users WHERE credits < 10;
```

### 5. 操作日志查询

```sql
-- 查询某个管理员的所有操作
SELECT * FROM admin_action_logs WHERE admin_id = 1 ORDER BY created_at DESC;

-- 查询针对某个用户的所有管理操作
SELECT * FROM admin_action_logs WHERE target_user_id = 123 ORDER BY created_at DESC;

-- 查询隐私数据查看记录（超级管理员查看对话、图片）
SELECT * FROM admin_action_logs
WHERE action_type IN ('view_conversation', 'view_image')
ORDER BY created_at DESC;

-- 查询最近24小时的管理员操作（PostgreSQL语法）
SELECT * FROM admin_action_logs
WHERE created_at > NOW() - INTERVAL '24 hours'
ORDER BY created_at DESC;
```

---

## 五、数据安全和隐私保护

### 1. 敏感信息加密

- **密码**: 使用 bcrypt 加密存储（不可逆）
- **API密钥**: 使用 AES-256 加密存储
- **微信OpenID**: 不直接暴露给前端

### 2. 操作审计

所有超级管理员查看用户隐私数据的操作都会记录到 `admin_action_logs` 表：

```sql
-- 示例：记录超级管理员查看用户对话
INSERT INTO admin_action_logs (
  admin_id,
  admin_role,
  action_type,
  action_description,
  target_user_id,
  target_resource_type,
  target_resource_id,
  reason,
  ip_address
) VALUES (
  1,                          -- 超级管理员ID
  'super_admin',
  'view_conversation',
  '查看用户对话详情',
  123,                        -- 目标用户ID
  'conversation',
  456,                        -- 对话ID
  '用户投诉处理',             -- 查看原因
  '192.168.1.100'
);
```

### 3. 数据访问控制

**Supabase Row Level Security (RLS)**:
```sql
-- 启用 RLS
ALTER TABLE users ENABLE ROW LEVEL SECURITY;

-- 用户只能访问自己的数据
CREATE POLICY "Users can view own data" ON users
  FOR SELECT USING (auth.uid() = id);

-- 管理员可以查看所有用户
CREATE POLICY "Admins can view all users" ON users
  FOR SELECT USING (
    EXISTS (
      SELECT 1 FROM users
      WHERE id = auth.uid() AND role IN ('admin', 'super_admin')
    )
  );
```

**后端API权限验证**:
```python
# 示例：查看用户对话API
@app.get("/api/admin/users/{user_id}/conversations")
async def get_user_conversations(user_id: int, current_user: User = Depends(require_super_admin)):
    # 记录操作日志
    await log_admin_action(
        admin_id=current_user.id,
        action_type="view_conversation",
        target_user_id=user_id,
        reason=request.query_params.get("reason"),
        ip_address=request.client.host
    )

    # 返回数据
    conversations = await get_conversations(user_id)
    return conversations
```

---

## 六、备份和恢复

### 1. Supabase 自动备份

Supabase 提供自动备份功能：
- **免费版**: 每日备份，保留7天
- **Pro版**: 每日备份，保留30天，支持PITR（时间点恢复）

在 Supabase Dashboard → Settings → Database → Backups 中查看和管理备份。

### 2. 手动导出数据

```bash
# 使用 pg_dump 导出（需要数据库连接字符串）
pg_dump "postgresql://postgres:[password]@db.your-project.supabase.co:5432/postgres" > backup_$(date +%Y%m%d_%H%M%S).sql

# 仅导出特定表
pg_dump "postgresql://..." -t users -t conversations > partial_backup.sql

# 仅导出表结构
pg_dump "postgresql://..." --schema-only > schema_backup.sql
```

### 3. 数据恢复

```bash
# 恢复完整数据库
psql "postgresql://postgres:[password]@db.your-project.supabase.co:5432/postgres" < backup_20260120_120000.sql
```

### 4. 备份建议

- **自动备份**: 依赖 Supabase 自动备份功能
- **定期导出**: 每周手动导出一份到本地/OSS
- **异地存储**: 备份文件上传到阿里云OSS

---

## 七、性能优化建议

### 1. 索引优化

已创建的关键索引：
- `users`: phone, wechat_openid, role, status
- `conversations`: user_id, model_id, created_at
- `messages`: conversation_id
- `image_generations`: user_id, created_at
- `admin_action_logs`: admin_id, target_user_id, created_at

### 2. 查询优化

```sql
-- ❌ 避免：全表扫描
SELECT * FROM messages;

-- ✅ 推荐：使用索引
SELECT * FROM messages WHERE conversation_id = 123 ORDER BY created_at ASC;
```

### 3. 数据归档

对于历史数据较多的表，建议定期归档：

```sql
-- 归档1年前的对话记录到归档表（PostgreSQL语法）
INSERT INTO conversations_archive
SELECT * FROM conversations WHERE created_at < NOW() - INTERVAL '1 year';

DELETE FROM conversations WHERE created_at < NOW() - INTERVAL '1 year';
```

---

## 八、故障排查

### 1. 连接问题

```sql
-- 检查当前连接数（PostgreSQL）
SELECT count(*) FROM pg_stat_activity;

-- 检查最大连接数
SHOW max_connections;

-- 查看活跃连接详情
SELECT pid, usename, application_name, client_addr, state, query
FROM pg_stat_activity
WHERE state = 'active';
```

### 2. 性能问题

```sql
-- 查看慢查询（需启用 pg_stat_statements 扩展）
SELECT query, calls, mean_time, total_time
FROM pg_stat_statements
ORDER BY mean_time DESC
LIMIT 10;

-- 分析表统计信息
ANALYZE users;

-- 查看表大小
SELECT pg_size_pretty(pg_total_relation_size('messages'));
```

### 3. 数据一致性检查

```sql
-- 检查用户积分是否与历史记录一致
SELECT
  u.id,
  u.credits as current_balance,
  (SELECT balance_after FROM credits_history WHERE user_id = u.id ORDER BY created_at DESC LIMIT 1) as history_balance
FROM users u
WHERE u.credits != COALESCE(
  (SELECT balance_after FROM credits_history WHERE user_id = u.id ORDER BY created_at DESC LIMIT 1),
  0
);
```

---

## 九、常见问题 FAQ

### Q1: 如何添加新的管理员？

```sql
-- 方式1: 直接修改用户角色
UPDATE users SET role = 'admin' WHERE id = 123;

-- 方式2: 通过超级管理员在后台操作（推荐）
-- 在用户详情页面点击【设为管理员】按钮
```

### Q2: 如何重置用户密码？

```sql
-- 生成新密码的bcrypt hash，然后更新
UPDATE users SET password_hash = '$2b$10$新的hash值' WHERE id = 123;
```

### Q3: 如何查看某个模型的使用情况？

```sql
SELECT * FROM v_model_stats WHERE id = 5;
```

### Q4: 如何禁用用户账号？

```sql
UPDATE users SET status = 'disabled' WHERE id = 123;
```

### Q5: 默认模型可以关闭吗？

不建议关闭默认模型（`is_default = TRUE`），因为：
- 新用户注册后默认订阅这些模型
- 如果关闭，新用户将没有可用模型

如果必须关闭：
```sql
UPDATE models SET status = 'maintenance' WHERE id = 1;
```

---

## 十、附录

### A. 密码加密工具

使用 Python bcrypt 生成密码hash：

```python
import bcrypt

# 生成密码hash
password = "admin123"
hash_bytes = bcrypt.hashpw(password.encode(), bcrypt.gensalt())
print(hash_bytes.decode())

# 验证密码
is_valid = bcrypt.checkpw("admin123".encode(), hash_bytes)
print(is_valid)  # True
```

### B. 数据字典

完整的数据字典请参考: [TECH_ARCHITECTURE.md](../document/TECH_ARCHITECTURE.md#三数据库设计)

### C. ER图

```
users (用户)
  ├─ 1:N → conversations (对话)
  │         └─ 1:N → messages (消息)
  ├─ 1:N → image_generations (图片生成)
  ├─ 1:N → credits_history (积分历史)
  ├─ N:N → models (模型)
  │         └─ user_subscriptions (订阅关系)
  └─ 1:N → admin_action_logs (操作日志)
```

---

**文档版本**: v1.1
**最后更新**: 2026-01-20
**维护者**: 开发团队
