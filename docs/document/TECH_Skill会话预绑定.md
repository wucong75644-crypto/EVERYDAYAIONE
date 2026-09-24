# Skill 第二期第 3 步：会话预绑定

任务分支 `codex/task/20260924235539-session-skill-bindings`，基座及代码回滚点 `e53ff60f2d1eb3d99497449c892284a725ac1203`。

## 行为与实施边界

- 为组织内的用户会话增加固定 package/revision 的 binding；会话所有者明确操作，或同组织活跃 owner/admin 配置，可以创建/移除。绑定不是授权，使用时仍检查当前身份、assignment、业务权限与版本状态。HTTP 不接受通道主体，不扩展既有通道授权入口。
- API 只接受 Skill 身份与版本，不接受权限、来源、正文或客户端指定组织。模型的 `activate_skill` 仍只影响本 Turn；不新增模型绑定工具，不读取消息参数中的绑定配置。
- 每个新交互式 Actor Turn 先读取会话 binding 的固定版本，按创建顺序启用，再处理本轮用户选择和模型激活。相同 Skill 不重复加载；本轮选择不同版本时提示会话版本已固定，不替换。
- 最多 4 个 binding，沿用 Turn 的 4 Skill/48 KiB 预算。绑定不可用或超预算时停止新 Turn，不能跳过其限制后继续执行。多个 Skill 及原会话工具上限持续取交集。
- 首次暂停边界前先用既有 BEFORE_MODEL 安全点保存会话来源和固定候选（包括空绑定集），每次启用后再保存已激活正文/权限。恢复只使用原 checkpoint，不读取当前 binding，也不重渲染；缺少 Skill 状态的历史 checkpoint 同样不继承新绑定。删除 binding 仅影响新 Turn；权限撤销、禁用或正文漂移仍阻止原任务恢复。
- 新版发布不更新 binding，变更版本需用户移除后重新添加。计划任务不读取或继承 binding；不增加自动推荐。

## 任务地图

数据层新增会话绑定迁移、RLS、不可变约束及 repository；Web 服务复用现有身份、目录解析和权限检查。Actor source 读取固定候选，SkillRuntime 保存绑定来源并复用激活/恢复与工具交集。聊天执行引擎在手动选择前启用绑定并写安全点。界面复用现有输入区 Skill 入口，增加独立会话绑定面板。

验证覆盖真实临时 PostgreSQL 的固定版本、RLS/写权限及迁移回滚；JWT/API 身份和请求白名单；Actor 多 Skill、权限撤销、暂停恢复及删除隔离；前端会话切换、迟到响应、版本展示、增删失败与重试。沿用项目 Python/Node 依赖，不新增依赖，不修改旧 Runtime 平台路径。

## API 与数据

沿用 Bearer 用户认证；每次操作重新读取用户、组织和成员状态。组织从会话读取，不信任请求头或 JWT 中的组织声明。管理员替其他成员配置时，也只按会话所有者的业务权限解析候选。

| API | 输入与结果 |
| --- | --- |
| GET `/api/skills/conversations/{conversation_id}/bindings` | 返回固定版本摘要、`binding_id`、`available`；失效绑定仍展示 |
| POST 同一路径 | 仅接受 `{skill_id, revision}`，返回 `{binding_id}`；必须匹配当前可见的已发布版本 |
| DELETE `.../bindings/{binding_id}` | 删除该会话的指定 binding，幂等返回 204；不会改写任务或 checkpoint |

相同版本重复添加幂等；已绑定其他版本、请求版本过期或达到 4 项上限返回冲突。表内以会话与 skill_key 唯一，revision 外键绑定具体 package；不提供 UPDATE，连数据库所有者也不能原地改写。通过会话 advisory lock 串行化上限检查，避免并发超限。RLS 同时约束组织、会话所有者/当前组织管理员、活跃身份与受信写入口。Actor 使用 projection 读取，普通 runtime/projection 不能写 binding。

复用现有 `SKILL_CATALOG_ENABLED`、`SKILL_RUNTIME_ENABLED` 和 `VITE_SKILL_UI_ENABLED` 开关。运行开关关闭时保留原有不读 Skill 数据的行为；含绑定或已激活 Skill 的 checkpoint 不允许在关闭状态恢复。功能正常验收须同时开启目录与运行开关。

## 改动文件

| 文件 | 职责 |
| --- | --- |
| `backend/migrations/263_conversation_skill_bindings.sql`、对应 rollback | 新表、固定版本外键、RLS、并发上限与空表回退 |
| `backend/services/skills/binding_repository.py`、`bindings.py` | 固定候选读写、用户/管理员授权、公开摘要 |
| `backend/api/routes/skills.py` | 会话 binding 的 GET/POST/DELETE |
| `backend/services/skills/runtime_source.py`、`runtime.py` | 交互式会话读取、优先加载、固定版本与 checkpoint |
| `backend/services/skills/context.py`、`feedback.py` | 绑定来源说明、版本冲突反馈 |
| `backend/services/handlers/chat/execution_engine.py` | 首次快照、绑定先于本轮选择、失败停止 |
| `frontend/src/services/skills.ts` | 会话绑定 API 类型与调用 |
| `frontend/src/components/chat/input/SkillSelector.tsx`、`SessionSkillBindings.tsx` | 既有菜单内查看、固定、移除；会话切换隔离 |
| `backend/tests/test_skill_binding_api.py`、`test_skill_bindings_postgres.py`、`test_skill_session_bindings.py` | 新功能及权限、迁移、恢复回归 |
| `backend/tests/test_skill_runtime.py`、`test_skill_runtime_source.py`、`test_skill_assets_runtime.py` | 旧测试数据源显式提供空绑定；原断言保留 |
| `frontend/src/components/chat/input/__tests__/SessionSkillBindings.test.tsx`、`frontend/src/services/__tests__/skillBindings.test.ts` | UI 操作、迟到响应、版本锁定、请求白名单 |
| 本文、`TECH_ActorSkillRuntime.md`、`TECH_Skill聊天选择与反馈.md` | 当前合同、验收和回滚说明 |

## 本地验证结果（2026-09-25）

- 后端最终批次 **1,340 passed，6 skipped，0 failed**（30.50 秒）。6 个跳过项为默认不启用的真实模型评估；未调用外部模型。覆盖全部 Skill 用例及受影响聊天执行、Actor、工具策略、重放与暂停链路。7 项新增 PostgreSQL binding 测试实际运行通过，使用临时 Unix socket 数据库，不使用项目 DATABASE_URL。
- 前端定向 **24 passed**：会话绑定面板 8 项、服务请求 1 项、既有选择器与输入区 15 项。旧选择器动画测试出现 2 条 AnimatePresence 的 act 提示，断言通过；新增测试无失败。
- TypeScript app/node 检查、改动前端文件 ESLint（0 warning）及 `git diff --check` 通过。主工作树保持干净，旧 Runtime 平台禁止路径不存在。
- 回归过程中发现旧模拟数据源缺少新增的空绑定读取方法，已补齐测试夹具，保留全部原断言。新增验证同时覆盖首次激活前暂停、部分绑定已激活后暂停、绑定删除、新版本发布、空绑定快照与缺少 Skill 状态的旧快照。

后端复验入口（在任务工作树 `backend/`，PATH 包含 PostgreSQL 的 initdb/pg_ctl；使用项目现有 Python 环境）：

```bash
DATABASE_URL=postgresql://unused JWT_SECRET_KEY=skill-tests-only python -m pytest \
  tests/test_skill*.py tests/test_chat_execution_engine.py tests/test_chat_generation_executor.py \
  tests/test_chat_gateway_retry_integration.py tests/test_chat_tool_mixin.py tests/test_chat_tool_loop.py \
  tests/test_tool_policy.py tests/test_conversation_commands.py tests/test_replay_checkpoint_store.py \
  tests/test_tool_production_integration.py -q
```

## 生产业务验证步骤（尚未执行）

2026-09-25 用户已明确“提交部署”，开始通过受控入口发布本任务；实际技术发布结果以本次 `DEPLOY_RESULT` / `RELEASE_RESULT` 为准。以下业务验收步骤留待生产测试，不等同于服务健康检查。

1. 经受控 `deploy/release.sh` 发布本任务候选，并应用 263 迁移；保持 DB/NAS 原 revision 和 checkpoint。检查目录、运行和 UI 开关均已启用。
2. 在测试组织打开会话 A 的 Skill 菜单，进入“固定到当前会话”，添加两个工具声明存在交集的 Skill。发送未手动选择 Skill 的消息，确认两项在模型响应前已启用，实际工具调用遵守交集；本轮再选第三项时只进一步收窄。
3. 发布其中一项的新 revision；A 的绑定仍显示旧 revision，新消息也使用旧 revision。移除再添加才能改用新版本。切到会话 B，应看不到 A 的绑定，B 的消息不自动启用这些 Skill。
4. 暂停 A 的任务，通过另一客户端移除绑定或管理员配置修改，再恢复；原任务继续使用原 checkpoint 的版本与正文，之后新 Turn 使用新配置。刷新历史消息，旧启用记录不变化。
5. 用测试成员撤销业务权限或禁用测试 Skill assignment：新任务应停止在 Skill 加载阶段；已暂停任务恢复也应拒绝。还原权限后可按原 revision 恢复。普通成员不能改他人会话；同组织管理员可配置，同名其他组织 Skill 不可见。
6. 从该会话创建计划任务并运行，确认其不读取会话绑定、不携带绑定来源；让模型请求持久启用某 Skill，确认不会创建 binding。所有配置都须来自明确 UI/API 操作。

## 回滚点

代码基座 `e53ff60f2d1eb3d99497449c892284a725ac1203`。任务发布后保留工作树供测试，不合并 main、不执行验收清理。

本次 checkpoint 新增 `session_skill_ids`，旧代码不能解析含该字段的快照。回退前先停止新 Skill 使用并完成或通过现有入口取消未终结的绑定任务；不要让旧代码继续恢复这些快照。保存 DB/NAS/checkpoint 数据，通过受控发布入口形成并验证回退候选，待新版恢复能力可用后再恢复原快照。

263 为新增表，代码回退可以保留表和用户配置。反向迁移只允许空表，非空会原子拒绝，不能为了回退删除用户绑定或历史任务。本文不授权任何生产执行。
