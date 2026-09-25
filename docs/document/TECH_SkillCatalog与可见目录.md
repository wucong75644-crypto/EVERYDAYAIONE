# Skill 第一期第 2 步：Catalog 与按权限解析的可见目录

P2-4 已增加计划任务级预绑定与版本锁定，见 [计划任务 Skill](TECH_Skill计划任务预绑定与版本锁定.md)。scheduled 仅使用任务显式绑定的已审核 revision，不允许动态激活；本文交互式发现与激活约定不适用于计划任务。

P2-1 已增加组织管理员草稿、审核、受控 NAS 发布、废弃与禁用，见 [Skill 草稿审核与发布](TECH_Skill草稿审核与发布.md)。下文保留各期原始范围；当前状态和发布写权限以 P2-1 文档为准。

后续状态：P1-3 已实现默认关闭的 Actor Turn 激活与精确恢复，见 [Actor SkillRuntime](TECH_ActorSkillRuntime.md)。P1-4 为手动选择追加稳定 `skill_id`，见 [聊天 Skill 选择与反馈](TECH_Skill聊天选择与反馈.md)。下文“不接入聊天”为 P1-2 阶段边界。

## 范围与接口

新增 `GET /api/skills/available?conversation_id=<UUID>`，必须使用现有 Bearer 登录认证。
返回 JSON 数组，元素严格限定为：

```json
[
  {
    "skill_id": "report",
    "name": "业务报表",
    "revision": "v2",
    "description": "汇总业务报表",
    "triggers": ["汇总业务"],
    "source": "org",
    "model_selectable": false
  }
]
```

`skill_id` 是稳定的 `skill_key`，用于 P1-4 手动选择，不是数据库主键或授权凭据。`source` 仅为 `platform` / `org` 来源类别，不回传包的内部 provenance 字符串。不会返回 package/revision 数据库 ID、组织或用户 ID、正文、路径、哈希、内部权限配置或凭证。`model_selectable` 只控制模型是否可以主动选择，不限制已授权用户手动选择，也不会自行触发激活。

只接受 `conversation_id` 查询参数，额外参数返回 422。JWT 的 `sub` 确定 actor；查询数据库中的当前活跃用户、属于该 actor 的 user-scope 会话，再从会话记录确定组织，核验组织与成员均活跃。JWT 的组织声明和 `X-Org-Id` 不参与解析。不存在、别人的会话、channel 会话或不匹配的 scope_id 返回 404；用户/组织/成员失效返回 403；缺少或无效认证返回 401。

本 Web 接口的 scope/domain/mode 固定为 `user/general/interactive`，不能由客户端修改。纯解析器支持可信服务端提供 `channel`、`erp`、`scheduled/preflight` 上下文；本期不增加这些通道的公开授权入口。P1 assignment 必须属于组织，因此个人会话返回 `[]`，不推导隐式平台授权。

默认 `skill_catalog_enabled=false`。认证通过后关闭状态直接返回 `[]`，不构建数据库连接池、不查 Skill 表、不读取 NAS；此时不会查询会话有效性。响应使用 `Cache-Control: no-store`。开启时每次重新核验成员和声明所需的现有业务权限码，使用新的 `PermissionChecker(db)`，无目录或权限缓存；未知权限码和未开启的 Feature Flag 均不能放行。

## 数据与解析规则

新增迁移 `257_skill_catalog_metadata.sql`，仅给 P1-1 的 `skill_revisions` 增加 `catalog_metadata JSONB NOT NULL DEFAULT '{}'`。沿用原 revision 不可变触发器；修改声明必须发布新 revision，再显式切换 assignment。未改历史迁移 checksum、现有业务表、RLS 角色授权或旧 Runtime 平台路径。

发布阶段沿用 P1-1 已有的受控文件校验，新增校验并保存 frontmatter 的 `catalog` 字段；目录请求只查询数据库摘要，**不调用已有正文读取能力**。例如在已有 skill_key/revision/description 同级声明：

```yaml
catalog:
  name: 业务报表
  triggers: [汇总业务]
  model_selectable: false
  conversation_scopes: [user]
  agent_domains: [general]
  execution_modes: [interactive]
  actor_user_ids: []
  required_permissions: [order.view]
  required_feature_flags: []
  allowed_tool_names: []
```

所有字段可省略；默认 name 使用稳定 skill_key、triggers 为空、model_selectable=false、范围为 user/general/interactive、允许工具为空。空 actor_user_ids 表示不在组织成员权限之外限制具体 actor；非空时必须匹配。权限与 Feature Flag 声明要求全部满足；三种上下文维度必须分别在允许集合内，显式空集合无匹配。catalog 未知字段、非法类型和枚举拒绝发布，错误码不回显原始数据。description 沿用已发布 summary；旧 revision 的默认 `{}` 不会扫描 NAS 自动补齐。

解析先筛选本组织 enabled assignment 固定的 published revision，再检查 actor、scope、domain、mode、权限和开关。组织隔离由显式 SQL 过滤、DatabaseScope/数据库 RLS 和解析器复核共同保障。数据库只投影解析需要的字段，不查询正文、路径、哈希或任意 provenance。非法目录元数据导致安全失败，不放宽过滤。

同一 `skill_key` 在合格候选中只选一个版本：

1. assignment `priority` 越大越优先（延续 P1-1 契约）。
2. priority 相同，组织包优先于平台包。
3. 按 skill_key 和 package_id 稳定排序，与数据库返回顺序无关。

不同 key 即使显示 name 相同，也保留为独立条目。禁用、退役或当前上下文无权使用的组织版本不遮挡另一个合格的平台版本；不修改文件，不自动选择最新 revision，不修改 assignment。平台包同样必须显式分配给当前组织。

`effective_allowed_tool_names(platform, authorized, declared)` 是纯函数，返回三集合交集的 `frozenset`；缺少当前授权或 Skill 声明时结果为空，不扩展通配符、不改输入。本期没有接入 ToolPolicy，也没有将交集当作执行授权。

没有新增模型控制工具、UI、checkpoint、正文注入、激活入口或自动 activation audit。现有聊天执行链路未引用新解析器。

## 改动文件

| 文件 | 改动 |
| --- | --- |
| `backend/services/skills/contracts.py` | 不可变 catalog 元数据契约及保守默认值 |
| `backend/services/skills/storage.py` | 在已有发布校验中校验 catalog 元数据 |
| `backend/services/skills/repository.py` | 存储元数据，新增只读摘要投影 |
| `backend/services/skills/resolver.py` | 可信上下文、公共摘要、纯解析和工具集合交集 |
| `backend/services/skills/available.py` | 会话归属、组织成员与当前权限的服务端适配 |
| `backend/api/routes/skills.py`、`backend/main.py` | 认证 GET 接口与路由注册 |
| `backend/migrations/257_skill_catalog_metadata.sql` 及对应 rollback | 新增不可变元数据列与无数据丢失回滚门禁 |
| `backend/tests/test_skill_resolver.py`、`test_skill_available_api.py`、`test_skill_resolver_postgres.py` | 纯函数、HTTP 权限边界、真实 PostgreSQL 验证 |
| `backend/tests/test_skill_storage.py`、`test_skill_catalog_postgres.py` | 元数据发布校验与 P1-1 回归 fixture |
| 本文及 `TECH_Skill控制面与受控存储.md` | 当前契约、验证与回滚说明 |

## 定向验证

在任务工作树 `backend/` 使用安装项目依赖的 Python 运行：

```bash
DATABASE_URL=postgresql://unused JWT_SECRET_KEY=skill-tests-only \
python -m pytest tests/test_skill_resolver.py tests/test_skill_available_api.py \
tests/test_skill_storage.py tests/test_skill_catalog.py \
tests/test_skill_catalog_postgres.py tests/test_skill_resolver_postgres.py -q

DATABASE_URL=postgresql://unused JWT_SECRET_KEY=skill-tests-only \
python -m pytest tests/test_chat_execution_engine.py tests/test_chat_stream_finalize.py \
tests/test_tool_policy.py tests/test_tool_execution.py tests/test_model_gateway.py -q
```

真实 PostgreSQL 测试要求 PATH 含 initdb/pg_ctl；只使用测试创建的临时 Unix socket 实例，未连接 DATABASE_URL。未配置二进制时显式 skip，不视为数据库验证通过。本地共享 Python 环境缺少已锁定的 PyYAML 6.0.2，本任务将它装入独立临时目录并通过 PYTHONPATH 引入，没有更改主工作树或依赖锁定。

2026-09-18 本地结果：纯函数/认证接口/存储/开关 121 项通过，PostgreSQL 17 的迁移、RLS、仓储与回滚 33 项通过，既有聊天/流收尾/工具策略/工具执行/模型网关 640 项通过，共 **794 passed，0 skipped**。数据库测试最初因沙箱内 initdb 初始化失败，改用获准的临时实例后全部通过；不是跳过失败。工具交集用例枚举了三工具全集的全部 512 种子集组合，验证输出始终不大于任一输入。未部署或执行生产验证。

## 生产验证（仅在用户明确“提交部署”后）

1. 使用 `deploy/release.sh` 发布当前任务，核验迁移 257 账本/checksum 和 catalog_metadata 列；开关继续默认关闭。有效登录请求返回 `[]`，无认证请求拒绝。确认 NAS 未挂载时该接口和原聊天均可用。
2. 经授权启用开关后，使用两个测试组织的成员与各自会话验证目录隔离。通过已有受信控制面发布测试 revision 并分配 assignment，验证 priority、同优先级组织优先、版本固定、禁用与退役。不使用客户端 org/权限/Skill ID 赋权。
3. 对比返回字段白名单；用其他用户的会话、伪造 query 和 X-Org-Id 验证拒绝或忽略；撤销成员/业务权限后下一次请求立即反映。空目录、个人会话返回 `[]`。
4. 回归普通文本、流式聊天、已有工具调用、停止/恢复；核验无 Skill 正文注入、无新增激活审计、无真实工具权限变化。本地测试不替代这些生产验收。

## 回滚

首选关闭 `SKILL_CATALOG_ENABLED`，恢复空目录并保持既有聊天。代码基座/回滚点为 `8302753b623dd4cc1dd0d8e3da4d03324d67c004`（已含 P1-1）。回退代码时保持开关关闭，保留新增列与声明数据；P1-1 的严格 revision 契约不认识新增列，因此保留列时不能重新开启旧版本控制面。

仅在确需数据库回滚且走受控流程时执行 257 rollback：单事务锁定 revision 表，以表所有者检查全部组织的数据；有非空 metadata 即报 `SKILL_CATALOG_METADATA_NOT_EMPTY` 并回滚整个事务。仅所有 metadata 都为 `{}` 才移除列，保留 P1-1 包、版本、assignment、审计和 NAS 文件；FORCE RLS 保持开启。不能清空声明以绕过门禁。
