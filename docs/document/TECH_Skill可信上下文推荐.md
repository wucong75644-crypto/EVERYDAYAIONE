# Skill 第二期第 5 步：可信上下文推荐

任务分支：`codex/task/20260925232159-skill-recommendations`。基座/代码回滚参考：`b66f427b56d347403239fd83972020a922fc663b`。本任务实现推荐与反馈，不修改旧 Runtime 平台路径。2026-09-26 用户明确授权“提交部署”，通过受控入口发布任务候选；不合并 main、不清理工作树。

## 行为与边界

- 规则排序第一版，不依赖向量库、嵌入模型或外部服务。最多 3 个候选，每个包含稳定 Skill ID、固定 revision、已发布摘要和结构化原因。
- 先从已启用的组织 assignment 与 published revision 建目录，再经原 SkillResolver 过滤组织、成员、业务域、执行场景、权限和功能开关。推荐器重复过滤，模型侧继续过滤 `model_selectable`；优先级不能绕过这些检查。
- 可信上下文来自当前认证身份、会话组织与范围、ToolContext、PermissionChecker、ToolRegistry + ToolPolicy、已固定会话版本。Web 固定 general/interactive/user，不能传组织、权限、业务域、执行场景、会话绑定或工具名。Web 的 `permission_mode` 只表示用户当前选择的 auto/ask/plan，不能赋予工具执行权。
- 文件类型只使用 UI 明确选择的 `pdf/docx/xlsx/csv/pptx/image/text`。不推断文件正文、网页、消息、模型输出、自由文本 triggers 或标题。发布元数据新增 `recommended_file_types`，经原草稿—审核—发布流程生效；它只用于排序，不是发布/授权或工具许可条件。
- 当前版本以会话绑定匹配加 100、文件类型匹配加 20、已声明工具与当前可用工具有交集加 5 排序；同分沿用 assignment priority、组织优先、稳定 ID/包 ID。原因同时说明当前组织可用及适用业务域/执行场景。工具能力还与会话固定 Skill 的既有工具上限取交集；交集不是新授权。
- 与会话固定版本冲突的候选不推荐。模型不再推荐已经固定的 Skill，保留原用户预绑定行为。文件类型偏好只影响当前打开的 UI 建议区，不隐式传入 Turn 或生成永久会话绑定；模型侧使用服务器已有上下文，不凭用户正文推断类型。
- 建议不读取 NAS/正文/附件，不渲染正文，不写 assignment/绑定，不改变 SkillRuntime.directory 或工具上限。用户点击建议后沿用现有仅本条消息选择流程；发送时重新校验。模型只有显式 `activate_skill` 才能加载，禁用/撤权后即使旧建议还在也不能激活。
- 模型建议只包含 ID、revision、固定原因码和值，在发往模型的请求视图内添加，不持久写入消息或 checkpoint。压缩后可重建同一 Turn 提示；暂停恢复不重新推荐，不改快照协议；scheduled/preflight 不开启动态推荐。

## 交互

现有 Skill 弹层“仅本条消息”页顶部新增建议区，保持原选择列表和会话固定页。候选显示原因，一键选择；“不相关”记录反馈并暂时隐藏该建议。建议区可滚动，普通选择列表始终可达。没有候选显示“暂无建议，可自行选择 Skill”；推荐请求或审计失败显示“建议暂不可用，可从全部 Skill 中选择”。不同会话、模式或文件类型的迟到响应丢弃；UI 再次与当前可见目录核对 Skill ID 和版本。反馈失败不撤销用户选择。

管理端高级设置新增“推荐文件类型（仅用于建议）”，沿用原多选控件，无新发布入口。

## API 与数据

`POST /api/skills/recommendations`：

```json
{"conversation_id":"UUID","selected_file_types":["pdf"],"permission_mode":"plan"}
```

返回 `status=ready|disabled|unavailable`、可空 `recommendation_id`、最多 3 个 `candidates`。额外参数及未知类型拒绝（422），无身份 401，非本人个人会话 404，失效成员/组织 403。个人无组织会话返回空结果，没有隐式平台授权。响应 `Cache-Control: no-store`。

`POST /api/skills/recommendations/{recommendation_id}/feedback`：

```json
{"conversation_id":"UUID","skill_id":"report","revision":"v1","feedback":"not_relevant"}
```

用户反馈只接受 selected/dismissed/not_relevant；模型侧由现有激活入口记录 activated/activation_failed。用户不能冒充模型反馈。反馈必须对应本人、同会话、同组织、同 audience 的真实推荐候选和版本。同一候选的相同反馈重试幂等；不同反馈追加保留，包括模型失败后重试成功。反馈只作为后续分析数据，不在线改变排序或权限。

迁移 `265_skill_recommendations.sql` 新建：

- `skill_recommendation_audits`：组织、用户、会话、可空 Turn、audience、algorithm_version、白名单事实、候选 ID/revision/原因、时间。空候选也记录；关开关后推荐路径不访问 DB/Skill/NAS，不额外产生审计。审计保存失败则不返回未落盘的候选。
- `skill_recommendation_feedback`：推荐 ID、Skill ID/revision、反馈枚举、时间，唯一键保障幂等。无任意文本反馈。

两表启用 FORCE RLS，按组织、用户、有效成员关系与会话隔离；普通角色只有 SELECT/INSERT，无修改删除权限。审计不存 Skill 名称/描述、用户消息、文件名/路径/正文、网页、模型输出。日志只记录固定错误码，失败不打印 SQL 或原异常内容。

`recommended_file_types` 为空时从序列化中省略，旧审核哈希、NAS metadata 和 checkpoint 不变。非空值属于新的不可变 revision 元数据，旧二进制不认识，不能把新 revision 交给旧代码继续运行。

## 改动地图

| 文件 | 职责 |
| --- | --- |
| `backend/services/skills/recommendations.py` | 可信事实契约、候选/原因白名单、确定性排序与边界 |
| `backend/services/skills/recommendation_service.py` | Web/Actor 适配、工具能力事实、失败隔离 |
| `backend/services/skills/recommendation_repository.py` | 推荐证据与反馈持久化 |
| `backend/api/routes/skills.py` | 推荐和反馈 HTTP 接口 |
| `backend/services/skills/contracts.py` | 已发布文件类型声明，保留旧序列化 |
| `backend/services/skills/runtime.py` | 当前 Actor Skill 的模型建议视图和显式激活反馈 |
| `backend/core/config.py` | 独立后端开关 |
| `backend/migrations/265_skill_recommendations.sql` 与 rollback 文件 | 两表、RLS、权限与保留证据的回退门禁 |
| `frontend/src/components/chat/input/SkillRecommendations.tsx` | 建议、原因、类型选择、反馈 |
| `frontend/src/components/chat/input/SkillSelector.tsx`、`InputControls.tsx` | 沿用原手动选择并传当前模式 |
| `frontend/src/services/skills.ts`、`frontend/src/config/featureFlags.ts` | 请求契约与独立 UI 开关 |
| `frontend/src/components/admin/skills/SkillDraftEditor.tsx` | 草稿文件类型声明 |
| `backend/tests/test_skill_recommendations.py` | 排序、可信事实、模型惰性加载、工具能力、恢复与失败隔离 |
| `backend/tests/test_skill_recommendation_api.py` | HTTP 身份、输入白名单、开关和反馈 |
| `backend/tests/test_skill_recommendations_postgres.py` | 真实 RLS、禁用、审核发布、反馈幂等和迁移回退 |
| `backend/tests/test_skill_session_bindings.py` | 发布前修正快照回归断言，按类型化集合语义比较 |
| `frontend/src/components/chat/input/__tests__/SkillRecommendations.test.tsx` | UI 建议、显式选择、反馈、迟到响应和开关 |
| 本文与 `docs/document/TECH_Skill聊天选择与反馈.md` | 实施、验证、生产步骤与回滚说明 |

## 定向验证

使用现有 Python 环境，提供虚构的 DATABASE_URL/JWT_SECRET_KEY，不读取或连接生产；PostgreSQL 用测试夹具创建一次性数据库和 socket-only 服务。定向覆盖推荐规则/API/真实 RLS、当前 Actor 激活及目录、审核发布与旧正文哈希、会话预绑定、计划任务快照。前端覆盖建议组件、选择器、输入框和会话绑定，并执行 TypeScript/生产构建与定向 ESLint。

2026-09-26 发布前验证结果：

- 后端 12 个定向测试文件共 266 项全部通过（`PYTHONHASHSEED=13`，包含此前稳定复现顺序问题的种子）。其中本次新增的推荐规则/API/真实 PostgreSQL 56 项全部通过。
- 基线既有失败：`tests/test_skill_session_bindings.py::test_old_checkpoint_does_not_pick_up_new_session_configuration`。`effective_allowed_tool_names` 为 frozenset，序列化为 JSON 数组后，恢复时重建集合可能改变顺序，测试用字典/数组逐项比较而不是集合语义。`PYTHONHASHSEED=13` 下，将基座完整 backend 只读导出到临时目录，与当前工作树运行同一测试，两者均复现相同的顺序差异。发布前仅将该用例改为 `RuntimeCheckpoint.model_validate(...)` 的完整模型比较，所有字段仍校验，工具上限按集合语义比较；`checkpoint()` 与 `_restore()` 的运行逻辑未修改。
- 前端 6 个文件共 82 项通过（最终分批复验）：推荐建议 9、原选择器 11、输入框 4、会话绑定 8、管理面板 44、资产 6。新增测试没有 act 警告；原选择器存在既有 AnimatePresence act 提示。
- 定向 ESLint 通过；TypeScript 与推荐开关开启的生产构建通过。构建保留既有大体积 chunk 提示，无构建失败。
- `git diff --check` 通过；主工作树干净，改动均留在本任务工作树。
- 未调用付费真实模型。首次发布保持推荐默认关闭；部署、迁移与健康检查结果以受控入口的发布结果为准，不执行合并或工作树清理。

复验后端命令（在任务 backend 目录）：

```bash
PYTHONHASHSEED=13 DATABASE_URL=postgresql://test:test@127.0.0.1:1/test \
JWT_SECRET_KEY=skill-recommendation-local-test-only \
PATH=/opt/homebrew/opt/postgresql@16/bin:$PATH \
/Users/wucong/EVERYDAYAIONE/backend/venv/bin/python -m pytest \
  tests/test_skill_recommendations.py tests/test_skill_recommendation_api.py \
  tests/test_skill_recommendations_postgres.py tests/test_skill_runtime.py \
  tests/test_skill_runtime_actor.py tests/test_skill_runtime_source.py \
  tests/test_skill_available_api.py tests/test_skill_catalog.py tests/test_skill_storage.py \
  tests/test_skill_authoring_postgres.py tests/test_skill_session_bindings.py \
  tests/test_scheduled_skill_snapshots.py -q
```

前端复验使用 `npm run test:run --` 加上述 6 个相应测试文件；构建使用 `VITE_SKILL_UI_ENABLED=true VITE_SKILL_RECOMMENDATIONS_ENABLED=true npm run build`。

## 生产验证步骤（仅在明确“提交部署”之后）

1. 用 `deploy/release.sh` 发布当前任务候选并应用 265；首次保持 `SKILL_RECOMMENDATIONS_ENABLED=false`。确认原目录、手动选择、会话固定、普通聊天不变；推荐接口返回 disabled，不读推荐数据。
2. 在受控测试窗口开启 `SKILL_CATALOG_ENABLED=true`、`SKILL_RUNTIME_ENABLED=true`、`SKILL_RECOMMENDATIONS_ENABLED=true`；前端构建使用 `VITE_SKILL_UI_ENABLED=true`、`VITE_SKILL_RECOMMENDATIONS_ENABLED=true`。这些是验证准备步骤，本任务不自行改生产配置。
3. 在测试组织发布一个声明 pdf 类型的 Skill 和若干普通 Skill，打开建议区显式选择 PDF。确认最多 3 项，类型/上下文原因可解释；不点击、不发送时无 Skill 激活、正文读取或工具权限变化。
4. 点击建议并发送：确认沿用仅本条消息选择与原 Skill 反馈；让模型自行选择时确认必须有 activate_skill 调用。推荐不匹配时可忽略或点“不相关”，普通聊天正常。
5. 建议出现后禁用测试 Skill/撤销 assignment，确认刷新后消失，旧选择发送不能激活；异组织或失效成员请求被拒绝。固定旧版本再发布新版本，确认建议不越过会话版本锁。
6. 检查同组织/用户的推荐审计、selected/not_relevant 与模型 activated/activation_failed；确认无上传正文、网页或模型文本。检查空候选也有证据，另一个用户/组织不能读取或伪造反馈。
7. 关闭独立推荐开关，确认建议消失/返回 disabled，普通 Skill 选择与激活仍可用。检查 scheduled/preflight 与暂停恢复不新增推荐或动态激活。

## 回滚

优先关闭 `SKILL_RECOMMENDATIONS_ENABLED`；只撤回界面可关闭 `VITE_SKILL_RECOMMENDATIONS_ENABLED` 并重新构建。保留审计、反馈、NAS、revision 和 checkpoint，不影响当前手动/模型激活机制。

代码回退参考基座 `b66f427b56d347403239fd83972020a922fc663b`。若已发布非空 recommended_file_types 的 revision，回退旧二进制前须关闭 catalog/runtime 并处理相关活跃 Turn，保留全部数据；不要改写已发布元数据或快照来适配旧代码。通过当前任务形成受控回退提交，不自行执行发布或直接部署历史任务 SHA。

265 rollback 只允许空表，并锁表后核验；有审计或反馈即拒绝，不为回滚删除证据。关闭推荐不需要数据库回退。
