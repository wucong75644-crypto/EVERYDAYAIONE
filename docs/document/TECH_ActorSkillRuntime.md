# Skill 第一期第 3 步：Actor 内的 Turn SkillRuntime

## 边界与开关

只接入现有 `ConversationTurnRuntime → execution_engine → ToolExecutor`，不增加用户 UI、HTTP 激活接口、脚本执行或数据库迁移。沿用 P1-1/P1-2 的受控存储、目录权限、不可变 revision 和 assignment；未修改旧 Runtime 平台路径。

`SKILL_RUNTIME_ENABLED=false` 为默认值，必须同时开启 `SKILL_CATALOG_ENABLED` 才启用。只有 Actor Turn 执行可广告 `activate_skill`。关闭时不构造 SkillRepository/SkillStorage、不读 Skill 表或 NAS、不注入目录或正文，正常模型循环保持原行为。模型伪造控制调用时返回 `SKILL_RUNTIME_DISABLED`，不进入业务工具分发。

目录由服务端 ToolContext 提供 actor、组织、scope、domain、mode，沿用身份核验及 PermissionChecker，不接受模型提供组织或权限。只保留通过 P1-2 解析且 `model_selectable=true` 的条目。内部模型目录包含稳定 `skill_id=skill_key`，不改变 `/api/skills/available` 的字段白名单；显示名称不用于寻址，不暴露 NAS 路径、内部 package ID 或凭证。

## 激活、预算和工具收窄

模型显式调用 `activate_skill(skill_id, args?)` 才读取正文。目录和 checkpoint 未激活条目只含摘要元数据。正文示例：`汇总 {{args.topic}}，只使用当前允许的只读工具。`，对应调用参数为 `{"skill_id":"report","args":{"topic":"昨日订单"}}`。

模板只支持 `{{args.<名称>}}`，名称以小写字母开头，最多 32 个小写字母/数字/下划线。参数限字符串、有限数值和布尔值，必须与模板名称集合一致。不匹配时结构化返回 `required_args` 供下一次显式调用使用，不返回正文或参数值。没有属性访问、表达式求值、include、路径解析、环境变量或 Token 注入；替换值按字面处理，不递归展开。

固定预算按 UTF-8 字节计量，作为与模型供应商无关的保守文本 token 上限：

| 项目 | 上限 |
| --- | --- |
| 模型目录 | 32 条、12 KiB，按解析器优先级依次纳入可容纳条目 |
| checkpoint 目录元数据 | 64 KiB，不含路径或正文 |
| 单 Skill 原始正文 | 16 KiB |
| 单次 args | 16 个标量变量、规范化 JSON 4 KiB；完整控制参数 4 KiB + 256 B |
| 单 Skill 渲染正文 | 24 KiB |
| Turn 所有渲染正文 | 总计 48 KiB、最多 4 个 Skill；另有每项固定身份/权限提示头 |

超限拒绝激活，不截断正文；P1-1 的 1 MiB 文件读取上限仍先生效。渲染成功才更新状态，失败返回 `{ok:false, code:...}`，不回显底层异常的文件路径、SQL 或凭证。

同批只要包含 `activate_skill`，整个批次进入控制分支，不调用 `handler._execute_tool_calls` 或 ToolDispatcher。按顺序处理激活，其余业务调用均返回 `SKILL_ACTIVATION_BARRIER` 和“请下一轮重新请求”；激活失败、关闭开关或业务调用排在激活前也不执行业务 IO。先补齐 assistant/tool 消息配对，再追加受控正文。

同一 Turn 内同一 Skill 和参数摘要重复激活返回 `SKILL_ALREADY_ACTIVE`，不重复读正文、渲染或注入。更换参数返回 `SKILL_ALREADY_ACTIVE_WITH_DIFFERENT_ARGS`，需新 Turn。控制调用不触发普通业务工具的三次重复副作用停止器，仍受模型轮次预算约束。不同 Skill 的声明逐次取交集。

`effective_allowed_tool_names = 平台工具全集 ∩ 原 ToolContext 授权上限 ∩ 已激活 Skill 声明`。原有 `authorized_tool_names=None` 表示没有额外名称上限；显式空集合保持空。声明为空即收窄到空，未知名称和 `*` 不扩权。

有效上限同时用于下一轮广告和新建/复用 ToolExecutor 的 `allowed_tool_names`。实际调用仍经过 ToolPolicy、身份/业务权限、资源边界与确认机制；不能解除 plan 限制或绕过危险操作确认。供应商额外注入的 Google Search 在存在显式工具上限时关闭，避免过滤后重新扩权。`activate_skill` 是控制协议，不属于业务授权集合。

## 安全点与精确恢复

新增 `AFTER_SKILL_ACTIVATION` / `after_skill_activation`。所有控制结果、渲染正文和工具上限就位后，先通过现有 fencing checkpoint 写入，再归约暂停/取消或发送完成进度。暂停恢复从 `next_model_round` 继续；取消前不读正文，读取过程中失去执行权不发布激活状态。状态只属于当前 Turn，不跨 Turn 缓存。

checkpoint 的 `skill_runtime` 保存 schema version、turn_id、固定目录候选身份；每项激活的 skill_key、revision、正文 SHA-256、精确渲染结果与 SHA-256、参数摘要（规范化 JSON SHA-256/变量名/字节数）、激活时工具上限，以及 Turn 当前最终上限。恢复时授权变小也会写入后续 checkpoint，不能在下次恢复放宽。不另存原始参数副本。

`BEFORE_MODEL`、`AFTER_TOOL`、`AFTER_SKILL_ACTIVATION`、`BEFORE_COMMIT` 均持久化该状态。既有 `save_generation_checkpoint` 接受非空 text 安全点，无需修改 RPC/迁移。正文作为 system messages 保存；压缩若移除正文，下次模型调用前从精确渲染结果恢复一次。

恢复不查询最新目录或重新渲染。每个已激活项重新检查身份、当前业务权限、enabled assignment、同一 package 的同一 published revision、NAS 内容及正文哈希，再使用 checkpoint 的渲染文本与工具上限。assignment 切到 v2 后旧 Turn 仍读取 v1；原版本退役/丢失、文件移除、哈希漂移或权限撤销均停止恢复，不替换最新版、不从用户目录兜底。`commit_ready` 也须先校验 Skill。

开关关闭后，含已激活 Skill 的 checkpoint 返回 `SKILL_REPLAY_RUNTIME_DISABLED` 并停止，不能按普通上下文继续。无 Skill 的旧 checkpoint 保持原恢复行为。

## 改动文件

| 文件 | 改动 |
| --- | --- |
| `backend/services/skills/runtime.py` | Turn 控制调用、幂等、消息、工具上限、checkpoint 与恢复 |
| `backend/services/skills/renderer.py` | 预算、字面模板替换、参数摘要 |
| `backend/services/skills/runtime_source.py` | Actor 身份/权限适配、按需固定版本读取 |
| `backend/services/skills/repository.py`、`resolver.py` | 指定 published revision 查询、内部候选身份，公开摘要不变 |
| `backend/core/config.py` | 默认关闭的 Runtime 开关 |
| `backend/services/conversation_commands.py`、`conversation_turn_runtime.py`、`replay_checkpoint_store.py` | 新安全点、Turn 状态和持久字段 |
| `backend/services/handlers/chat/execution_engine.py` | 控制屏障、消息、下一轮广告和执行授权 |
| `backend/services/handlers/chat/executor.py` | checkpoint 边界映射、commit-ready 校验 |
| `backend/services/handlers/chat/stream_setup.py` | 内置搜索遵守工具上限 |
| `backend/services/handlers/chat_tool_mixin.py` | 新建/复用执行器应用授权上限 |
| `backend/tests/test_skill_runtime.py`、`test_skill_runtime_actor.py`、`test_skill_runtime_source.py` | 预算、安全模板、按需加载、屏障、暂停恢复、重试去重、取消、缺失/漂移、收窄、关闭回归 |
| `backend/tests/test_skill_catalog_postgres.py`、`test_chat_gateway_retry_integration.py` | 固定版本/RLS、内置搜索边界 |
| `backend/tests/test_tool_production_integration.py` | Actor 测试夹具显式声明 Skill 未启用，断言原工具上限不变 |
| 本文、`TECH_SkillCatalog与可见目录.md`、`TECH_Skill控制面与受控存储.md` | 当前契约与验证/回滚步骤 |

## 本地验证

在任务工作树 `backend/`，使用已安装项目依赖的 Python：

```bash
DATABASE_URL=postgresql://unused JWT_SECRET_KEY=skill-tests-only python -m pytest \
  tests/test_skill_runtime.py tests/test_skill_runtime_actor.py tests/test_skill_runtime_source.py \
  tests/test_skill_resolver.py tests/test_skill_available_api.py tests/test_skill_storage.py \
  tests/test_skill_catalog.py tests/test_chat_execution_engine.py tests/test_chat_generation_executor.py \
  tests/test_chat_gateway_retry_integration.py tests/test_chat_tool_mixin.py tests/test_chat_tool_loop.py \
  tests/test_conversation_commands.py tests/test_replay_checkpoint_store.py \
  tests/test_tool_policy.py tests/test_tool_execution.py -q

# 发布门禁发现的共享执行链路验证也纳入定向组。
DATABASE_URL=postgresql://unused JWT_SECRET_KEY=skill-tests-only python -m pytest \
  tests/test_tool_production_integration.py tests/test_skill_runtime.py \
  tests/test_skill_runtime_actor.py tests/test_skill_runtime_source.py -q

# PATH 需含 initdb/pg_ctl；测试只启动临时 Unix socket PostgreSQL。
DATABASE_URL=postgresql://unused JWT_SECRET_KEY=skill-tests-only python -m pytest \
  tests/test_skill_catalog_postgres.py tests/test_skill_resolver_postgres.py -q
```

2026-09-18 最终结果：第一组 859 项通过（含 50 项新增 Skill Runtime 定向用例），第二组 34 项通过，共 **893 passed、0 skipped**；`git diff --check` 通过。未调用外部模型或业务服务，未执行生产验证。数据库测试首次因沙箱限制无法初始化；获准启动临时实例后全部通过，不读取项目数据库凭证。

首次提交部署候选 `ecea89e9`：前端 1351 项通过并完成部署；后端完整门禁为 10365 passed、2 failed、37 skipped、4 xfailed，未进入后端同步。两项失败是既有共享 Chat 集成测试的无约束 Mock 将新增 `skill_runtime` 字段自动构造成 Mock。夹具已显式设为 `None`，并增加关闭状态下 ToolContext 授权上限保持原样的断言；不改生产逻辑、不弱化原有权限/安全点断言。扩大定向组 168 项全部通过。首次发布不构成完整候选，最终发布状态以受控入口的 `RELEASE_RESULT` 为准。

## 生产验证步骤（待明确“提交部署”授权）

1. 通过 `deploy/release.sh` 发布任务候选，默认保持 Runtime 开关关闭。回归文本、普通工具、暂停/取消和旧 checkpoint，确认没有 `activate_skill` 广告或 Skill NAS 读取，无新迁移。
2. 经授权在测试组织开启两个开关，用受信控制面发布/分配 `model_selectable=true` 的只读 Skill v1，声明只允许 `file_search`。普通消息只读摘要，显式激活才读正文；未知 ID、超限、非法模板或参数返回结构化失败。
3. 用受控模型响应/请求记录验证混合批次业务 IO 为零，下一轮只广告交集工具，重复激活不重复注入。强行请求未允许的工具必须被策略拒绝，危险操作仍需原有确认，plan 限制不变。
4. 激活后暂停，核验 checkpoint 的 revision、SHA、渲染结果、参数摘要、工具上限和安全点；恢复后模型回合连续、正文不重复。验证执行尝试重试、取消、组织/通道隔离和权限撤销。
5. 切换测试 assignment 到 v2，旧 Turn 恢复仍须 v1。仅在专用测试 Skill 副本上做文件移除/哈希漂移，确认恢复停止，再还原原始字节；不改正式 Skill，也不以最新版替换旧 checkpoint。

## 回滚点与限制

任务基座：`0c1b0313f91772caefe426e83c0d4d8db6b331d2`（含 P1-1/P1-2）。任务分支：`codex/task/20260918214905-skill-runtime-p1-3`。

首选关闭 `SKILL_RUNTIME_ENABLED`，新 Turn 回到既有行为，已激活的暂停/重试 checkpoint 安全停止。保留 revision、NAS 原文件和 checkpoint，可在重新开启后恢复原版本。

回退代码到基座前，必须完成或通过现有取消入口终止含已激活 Skill checkpoint 的未终结任务；旧代码不认识 Skill 上限，不能恢复这些任务。无需数据库回滚/删除数据。本任务不自动部署、合并 main、清理工作树或修改生产开关。
