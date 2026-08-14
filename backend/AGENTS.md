# Backend Rules

本文件补充根 `AGENTS.md`，仅适用于 `backend/`。

## Python

- 使用项目现有 venv 和 `python3`；优先通过 `scripts/run_tests.sh` 运行测试。
- 新增或修改的公共函数必须有类型注解。
- 资源操作优先使用 context manager。
- async 流程必须处理取消、超时和有业务上下文的错误日志。
- 不硬编码密码、Token、API Key、数据库 URL 或生产配置。

## API 与服务

- 保持现有响应和错误合同；破坏性 API 变化必须经过 A级确认和版本设计。
- 超过 1 秒的外部或重操作应评估异步、超时、取消和幂等。
- AI、ERP和外部调用根据真实副作用设计 retry/reconcile，不能只解析错误字符串推断安全重试。
- 权限检查以后端和数据库事实为准，前端隐藏不能作为安全边界。

## 数据库

- Schema、函数、权限或约束变化必须使用新的 migration，并提供精确 rollback。
- 不修改已应用 migration 的身份或历史事实。
- 分析并验证事务、锁序、并发、幂等、RLS、角色权限、失败关闭和回滚。
- Worker、Runtime、WeCom、Sync 等角色只获得必要的窄能力，不开放核心表直权。
- 真实 PostgreSQL 或 External 测试只在风险需要且目标隔离明确时运行。

## 验证

- 先运行定向测试，再按调用链和风险扩大。
- Python/SQL 改动执行语法或编译检查、相关测试和 `git diff --check`。
- migration 变更验证发现顺序、rollback 映射；高风险数据库变更验证 apply → rollback → reapply。
