# 🛠️ 项目工具脚本

本目录包含项目维护和管理的各种脚本工具。

## 📁 目录结构

```
scripts/
├── security/                          # 安全相关脚本
│   ├── update_env.sh                  # 更新环境变量密钥
│   └── verify_supabase.py             # 验证 Supabase 连接
├── database/                          # 数据库相关脚本
│   ├── apply_migration.sh             # 应用数据库迁移
│   ├── run_migration.py               # 执行迁移脚本
│   └── check_fk_constraints.sql       # 检查外键约束
├── clean_placeholder_messages.py      # 清理占位符消息
├── fix_orphan_tasks.py                # 修复孤儿任务
├── manual_process_task.py             # 手动处理任务
├── check_conversation_messages.py     # 检查对话消息
├── clean_invalid_completion_messages.py # 清理无效完成消息
├── clean_media_messages.py            # 清理媒体消息
├── diagnose_media_messages.py         # 诊断媒体消息
├── test_message_system.py             # E2E 消息系统测试
└── README.md                          # 本文件
```

---

## 🔐 安全脚本 (security/)

### update_env.sh

**用途**：交互式更新 Supabase 密钥

**使用场景**：
- 重置 Supabase Service Role Key 后更新 .env 文件
- 密钥泄露后紧急更换

**用法**：
```bash
bash scripts/security/update_env.sh
```

**功能**：
- 自动备份原 .env 文件
- 交互式输入新密钥
- 支持同时更新 anon key

---

### verify_supabase.py

**用途**：验证 Supabase 连接和密钥有效性

**使用场景**：
- 更新密钥后验证连接
- 排查数据库连接问题
- 确认 .env 配置正确

**用法**：
```bash
cd backend
source venv/bin/activate
python3 ../scripts/security/verify_supabase.py
```

**输出示例**：
```
🔍 验证 Supabase 密钥...
📡 URL: https://qcaatwmlzqqnzfjdzlzm.supabase.co
🔑 Service Key: eyJhbGciOiJIUzI1N...
✅ 连接成功！
✅ 数据库响应正常
📊 用户总数: 42
🎉 密钥验证通过！
```

---

## 💾 数据库脚本 (database/)

### apply_migration.sh

**用途**：显示数据库迁移指令

**使用场景**：
- 应用新的数据库迁移
- 查看迁移 SQL 内容

**用法**：
```bash
bash scripts/database/apply_migration.sh
```

**输出**：
- 显示需要在 Supabase Dashboard 执行的 SQL
- 提供 Dashboard 链接

### run_migration.py

**用途**：通过 Supabase RPC 执行数据库迁移 SQL

**用法**：
```bash
cd backend && source venv/bin/activate
python3 ../scripts/database/run_migration.py
```

---

## 🔧 维护脚本

### clean_placeholder_messages.py
清理前端占位符遗留的"生成完成"消息。

### fix_orphan_tasks.py
修复已完成但消息未创建的孤儿任务。

### manual_process_task.py
手动处理指定的已完成任务（用于修复消息未创建的情况）。

**维护脚本用法**：
```bash
cd backend && source venv/bin/activate
python3 ../scripts/fix_orphan_tasks.py
```

---

## ⚠️ 使用注意事项

### 环境要求
- Python 3.12+
- 已激活的虚拟环境 (backend/venv)
- 正确配置的 .env 文件

### 安全提醒
- ❌ 不要将这些脚本提交到公共仓库（如果包含敏感信息）
- ✅ 脚本会自动备份原文件
- ✅ 所有密钥输入不会显示在终端

### 权限管理
所有脚本都设置了可执行权限：
```bash
chmod +x scripts/**/*.sh
chmod +x scripts/**/*.py
```

---

## 📝 添加新脚本

如果需要添加新的工具脚本：

1. **选择合适的目录**：
   - 安全相关 → `security/`
   - 数据库相关 → `database/`
   - 部署相关 → 创建 `deploy/`
   - 测试相关 → 创建 `testing/`

2. **脚本命名规范**：
   - 使用小写字母和下划线
   - Shell 脚本：`.sh` 后缀
   - Python 脚本：`.py` 后缀
   - 示例：`backup_database.sh`, `seed_data.py`

3. **添加文档**：
   - 在脚本开头添加注释说明用途
   - 在本 README 中添加使用说明

4. **设置权限**：
   ```bash
   chmod +x scripts/path/to/script
   ```

---

## 🔗 相关文档

- [安全检查清单](../docs/guides/SECURITY_CHECKLIST.md)
- [安全修复报告](../docs/guides/SECURITY_FIXES_2026-01-28.md)
- [数据库迁移](../docs/database/migrations/)

---

**最后更新**: 2026-03-08
