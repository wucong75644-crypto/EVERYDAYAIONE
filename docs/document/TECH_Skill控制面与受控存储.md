# Skill 第一期第 1 步：控制面与受控 NAS 存储

本页保留 P1-1 的交付边界。P1-2 的摘要接口、版本优先级和权限解析见 [Skill Catalog 与可见目录](TECH_SkillCatalog与可见目录.md)；P1-1 控制面及正文读取能力仍未接入聊天。

## 范围与代码边界

本期提供内部 Python 契约、仓储、包校验和默认关闭的控制面服务。没有 HTTP 路由、UI、自动导入、模型调用、工具注册、提示词注入或运行时激活。
不修改 `execution_engine`、`ConversationTurnRuntime`、`ToolPolicy`，也不修改旧 Runtime 平台路径。

| 文件 | 职责 |
| --- | --- |
| `backend/migrations/256_skill_catalog.sql` | 仅新增四张 Skill 表、索引、约束、触发器和 RLS |
| `backend/migrations/rollback/256_skill_catalog_rollback.sql` | 空目录数据库回滚；有数据时拒绝删除 |
| `backend/services/skills/contracts.py` | 冻结的包、版本、assignment、审计输入及校验结果 |
| `backend/services/skills/storage.py` | NAS 文件边界、frontmatter 和双 SHA-256 校验 |
| `backend/services/skills/repository.py` | 固定 DatabaseScope 的事务仓储 |
| `backend/services/skills/catalog.py` | 所有控制面入口先检查 Feature Flag |
| `backend/services/skills/__init__.py` | 无注册、无启动副作用 |
| `backend/core/config.py` | 新增两个环境配置字段 |
| `backend/tests/test_skill_storage.py` | 文件和内容安全校验 |
| `backend/tests/test_skill_catalog.py` | 默认关闭、关闭时零 DB/文件访问、禁用 assignment |
| `backend/tests/test_skill_catalog_postgres.py` | 实际迁移、回滚、RLS、仓储和端到端控制面 |

## 数据契约

- `skill_packages`：稳定 `skill_key`、非空来源标识 `source`、`scope_kind=platform/org`、`org_id`。platform 的组织为空，org 必填。skill_key 在各归属命名空间内唯一，跨组织可同名。包身份不可更新或删除。
- `skill_revisions`：每个包的 revision 唯一；记录相对 NAS 路径、整份文件哈希、正文哈希、摘要、状态和创建时间。发布只 INSERT，重复发布报错；数据库禁止改身份、路径、哈希、摘要和创建时间，禁止删除。状态只允许 `published → retired`，退役不可恢复，需要新 revision。
- `skill_assignments`：每组织/包一行，固定到明确 revision；默认 `enabled=false`，整型 priority 越大越优先，同优先级按 skill_key、package_id 稳定排序。不定义同名 Skill 的运行时覆盖规则。本期只返回目录记录。
- `skill_activation_audits`：预留组织、包/版本、actor、conversation、turn、request、outcome、reason_code、时间。仅追加，不记录正文。outcome 为 activated/skipped/failed；本期只提供显式仓储写入能力，不自动生成激活事件。

assignment 和审计通过复合外键保证 revision 属于指定包，触发器防止引用外组织包。外键使用 RESTRICT，不给现有组织和聊天数据增加级联删除行为。

仓储复用 `get_db().pool` 和 `DatabaseScope`，每次事务注入现有 `app.*` 身份。仓储查询显式约束组织，数据库额外启用并强制 RLS，仅对既有 backend 服务角色 `everydayai` 授予所需权限。组织范围可读平台包和本组织包；只能发布/退役自己归属的包；平台范围只能修改平台包。控制写入要求可信的 `runtime_admin` access kind；此枚举仅复用数据库身份，不调用任何 Runtime。

本期服务是受信任 backend 内部能力，构造 DatabaseScope 不等于验证用户管理权限。后续若增加 HTTP 管理接口，必须接入已有真实管理员授权，不能从请求体直接构造 scope。没有向 Runtime 数据库角色授予 Skill 权限。

## 受控 NAS 与校验

环境配置：

```text
SKILL_CATALOG_ENABLED=false
# 开启后必须显式指定，不能指向用户工作空间：
SKILL_STORAGE_ROOT=/mnt/platform-skills
```

`SKILL_STORAGE_ROOT` 默认未配置。关闭时构造 catalog 无文件/数据库访问，所有业务入口立即返回 `SKILL_CATALOG_DISABLED`，不会因缺少目录影响应用启动。开启时的包读取要求根目录已经存在，且 realpath 与 `FILE_WORKSPACE_ROOT` 互不包含。

固定文件布局：

```text
platform/<skill_key>/<revision>/SKILL.md
org/<org_uuid>/<skill_key>/<revision>/SKILL.md
```

调用者提供包身份与期望哈希，不能自由选择读取路径。数据库记录相对路径；发布、读取时均须匹配上述身份路径。路径校验拒绝绝对路径、`..`、反斜杠、非规范路径和 NUL；realpath 必须在受控根内。逐目录 `dir_fd + O_NOFOLLOW` 打开，拒绝目录/文件符号链接和多硬链接文件，避免校验后的符号链接替换。只接受普通文件，最多 1 MiB，UTF-8。

最小 SKILL.md：

```markdown
---
skill_key: report
revision: "v1"
description: 生成组织业务报表
---
# 使用说明
这里是 Skill 正文。
```

YAML 使用现有 PyYAML 6.0.2 SafeLoader，拒绝重复键、非字符串键、别名和不安全标签。解析器对非法日期、超长整数或错误映射抛出的 ValueError/TypeError 统一为 `SKILL_FRONTMATTER_INVALID`；已有 SkillError 错误码原样保留。skill_key/revision 必须与发布契约逐字一致，revision 必须是字符串（纯数字版本请加引号），description 为非空字符串，最长 2000 字符；正文必须非空。额外元数据不会被执行或接入工具。

- `content_sha256`：原始 `SKILL.md` 全部字节，含 frontmatter。
- `body_sha256`：结束 `---` 所在行及其换行之后的所有 UTF-8 字节；不 trim、不替换换行。
- 发布和每次读取都校验双哈希，任何差异立即报错，不回退工作空间。

平台发布者预先把经过审核的文件放入新的唯一版本目录，再调用 catalog 发布；backend 对 NAS 只需读取权限。此期不提供 NAS 写入/覆盖接口，不扫描用户工作空间。已有发布文件应由平台目录权限/只读挂载保护；如果运维直接改写了文件，应用下一次读取会拒绝。NAS ACL、挂载及文件不可覆盖属于部署环境前提，本期未改动基础设施。

控制面调用顺序：受信任管理端构造 `SkillRepository(get_db().pool, scope)`，传给 `SkillCatalog(repository, settings)`，调用 create_package → publish_revision → 组织 set_assignment。read_assigned_skill 只接受本组织启用且状态为 published 的版本，读取期间复核内容；此调用没有接入聊天。

## 定向验证

从任务工作树 `backend/` 使用已安装依赖的 Python 环境，设置虚拟测试配置，避免加载任何真实凭据：

```bash
DATABASE_URL=postgresql://unused JWT_SECRET_KEY=skill-tests-only \
python -m pytest tests/test_skill_storage.py tests/test_skill_catalog.py tests/test_skill_catalog_postgres.py -q

DATABASE_URL=postgresql://unused JWT_SECRET_KEY=skill-tests-only \
python -m pytest tests/test_chat_execution_engine.py tests/test_chat_stream_finalize.py \
tests/test_tool_policy.py tests/test_tool_execution.py tests/test_model_gateway.py -q
```

PostgreSQL 测试要求 PATH 含 `initdb`、`pg_ctl`，仅创建临时 Unix socket 实例，无 TCP 监听，不连接 DATABASE_URL；没有二进制时显式 skip，不能把 skip 当作迁移执行成功。受限沙箱若禁止 PostgreSQL 共享内存，需要允许该本地测试操作。

修复后验证（2026-09-18）：文件/契约/开关测试 59 项、临时 PostgreSQL 17 迁移及仓储测试 26 项全部通过，共 85 项，没有 skip。新增 8 项覆盖 YAML 构造异常、非 BYPASSRLS 表所有者的空库回滚/重建、三种组织上下文下隐藏数据保护与失败后 RLS 恢复、无权限服务账号拒绝回滚。此前既有聊天执行/流收尾/工具策略/工具执行/模型网关的 640 项回归已通过；本次未改这些调用链，复用该验证证据。尚未执行生产迁移、真实 NAS 权限验证或生产聊天验收。

## 生产验证与回滚（待用户明确“提交部署”后执行）

1. 经 `deploy/release.sh` 受控流程发布，检查迁移账本中 `256_skill_catalog.sql` 成功且 checksum 一致，确认四表、复合外键、不可变触发器和 RLS；迁移不修改既有表/函数。先在生产同版本 PostgreSQL 的临时验证库演练 up → 空库 down → up。
2. 检查实际生效的 `settings.skill_catalog_enabled is False`，包括所有 worker 的环境覆盖；保持关闭，`SKILL_STORAGE_ROOT` 未挂载不应影响启动。
3. 回归 Web/企微现有聊天：普通文本、模型流式输出、已有工具调用、停止/恢复。确认没有 Skill 正文注入、没有新增激活记录和工具权限变化。上述本地回归测试通过不替代生产通道验证。
4. 后续单独启用控制面时，先核验独立受控 NAS 根、只读 backend 权限、唯一版本目录，再验证两个测试组织隔离、禁用 assignment、退役版本、非法路径和篡改拒绝。不要在默认关闭验证过程中修改生产开关。

代码回滚点为本任务基座 `d695eaac8ef376795fc62bf590d9c95b400c676b`。回退代码时保持开关关闭，新表可保留，不影响旧代码。数据库 rollback SQL 必须在单一事务内执行：先以 ACCESS EXCLUSIVE 锁定四表，临时解除四表对所有者的 FORCE RLS，并设置事务级 `row_security=off`，确保空表检查不会静默遗漏其他组织的数据。只有全部为空才删除新增对象；已有包或审计时返回 `SKILL_CATALOG_NOT_EMPTY`，整段事务撤销，数据和 FORCE RLS 恢复，不自动清除后重试。账号必须具有四表及新增函数的所有者权限；权限不足立即失败，不新增授权。正式回滚及账本对账须遵守发布流程，不能直接改历史迁移内容或绕过账本。NAS 文件不由数据库 rollback 删除。
