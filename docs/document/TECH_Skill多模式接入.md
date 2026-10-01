# Skill 多模式接入

2026-10-01。任务分支 `codex/task/20261001142219-skill-multimode`，稳定基座 `260ed1bffb92896653542e41ffe4701ecc755c6e`。仅开发与本地验证；未提交、推送、部署、合并 main 或关闭工作树。

## 需求和行为

用户要求将 Skill 接入智能菜单的全部现有模式。沿用已确认的输入布局：附件位置、Skill 弹层锚点、文字首行标签和紧凑范围图标不变。推荐按模式筛选；当前任务已在过程说明中采用这一方案，用户未选择“所有模式共用全部 Skill”。

| 模式 | Skill 行为 | 保持的执行流程 |
| --- | --- | --- |
| 智能 `smart` | 现有手动选择、会话固定、模型显式 `activate_skill` | 聊天 Actor、工具权限和审批 |
| 图生图 `image-i2i` | 用户选择或兼容的会话固定方法，结合参考图准备生成提示词 | 必须有原图；原模型、比例、分辨率、格式、数量、积分和回调 |
| 文生图 `image-t2i` | 根据方法与可选补充文字准备提示词 | 原图片生成链路及参数 |
| 电商图 `image-ecom` | 方法用于已有策划模型，资料够时可只上传商品图 | 返回方案卡片，用户确认后才批量出图 |
| 视频 `video` | 根据方法、可选补充文字和首张参考图准备提示词 | 原视频模型选择、参数、积分、任务和回调 |

选择 Skill 或有可用兼容固定项时允许正文为空；这是提交任务意图，不表示必需资料可以省略。图生图仍在前后端检查图片；方法缺少必要资料时提示补充。没有选中/固定 Skill 的请求不会加载正文，也不会多调用提示词模型。

第一版媒体接入负责把已加载的方法用于图片/视频任务，不为媒体准备模型提供文件读取、网络访问或其他业务工具。需要这些能力的方法必须要求补充资料或留在智能模式处理。聊天模式保留完整的原工具与 `activate_skill` 流程；媒体准备模型不按推荐自动选择额外 Skill。

## 元数据、目录与会话

- 新增 `catalog.task_modes`：`smart`、`image-i2i`、`image-t2i`、`image-ecom`、`video`，至少一项，最多五项。旧元数据默认只有 `smart`；默认字段不序列化进旧元数据，以保持旧审核哈希和重放合同。
- 管理界面“高级设置 → 适用模式”可明确选择。修改已发布方法仍须新版本、审核、发布及组织授权；本任务不改写或自动发布现有 Skill，不把“参考图多方案提示词”自动改成出图方法。
- `/skills/available`、推荐和绑定查询接受类型受限的 `task_mode`，默认 `smart`；这是筛选意图，不是组织、用户、权限或发布授权。目录仍从当前会话、活跃成员身份、已发布版本和业务权限解析。
- 公开摘要增加 `task_modes`。前端兼容旧接口缺少该字段时默认 `smart`，并再次过滤不兼容目录和错误推荐。
- 固定项仍存储稳定 ID 和明确版本；切换模式只隐藏不适用项，不删除绑定。四项容量是整个会话的总量。适用但失效或撤权的固定项必须报错，不能降级成普通生成。
- 绑定写入或网络结果不确定时，切换模式仍阻止发送，完成后按新模式核对服务器。迟到目录、绑定和推荐响应不会污染其他会话或模式；不兼容的仅本条选择会清除。
- 推荐审计 `facts` 增加 `task_mode`；媒体推荐工具能力为空，与实际准备阶段一致。推荐原因可显示“明确支持当前任务模式”。原选择/不相关反馈与最多三项规则保留。

## 执行与授权边界

`InputArea → useInputSubmission → useMediaMessageHandler → sendMessage → GenerateRequest → message route → 原媒体 Handler`。

`selected_skill` 与新可选 `skill_task_mode` 均为顶层类型字段，不拼入用户正文。后端校验模式与实际生成类型一致，图生图必须有图片；客户端伪造的 `_selected_skill`、`_skill_task_mode`、`_media_skills` 会清除，再从类型化意图填入内部数据。幂等指纹包含顶层模式和 Skill 身份，忽略运行时内部字段。

`services/skills/media.py` 复用 `create_skill_runtime`、`activate_session`、`activate_manual`，保持组织/权限/版本/文件哈希/模板/资源预算检查。仅激活用户选择和适用的固定版本；不会因目录或推荐加载方法正文。媒体上下文的工具上限为空，模型没有可调用工具。

直接图片/视频使用已有 ModelGateway 和 `image_enhance_model` / `image_enhance_vl_model` / `image_enhance_timeout` 准备一次提示词。输出只接受有界 JSON 的 `prompt`、`input_required` 两个字符串；未知字段、错误格式、空提示词或缺项均失败。模型只提供生成内容，不能改变模式、模型、数量、参数、参考图地址或权限。缺项/失败发生在生成 Provider 提交和积分锁定之前（图片入口保留原余额预检）。辅助模型有实际 API 请求成本和等待时间；本任务不修改图片/视频用户积分价格。

电商图复用现有策划调用，不额外调用一次提示词模型。Skill 指令用于方法，平台方案格式保留；空正文且有商品图时允许从图中识别商品，不确定的资料要求补充。已有确认方案直接使用其冻结提示词；确认请求若另选新 Skill 会提示重新策划，不悄悄替换方案。

原始用户正文、附件和历史消息不改写；生成任务保存准备后的提示词供原重试流程复用。`request_params._media_skills` 只保存激活 ID、版本、正文/渲染哈希和选择来源，不保存方法正文或模型生成的授权声明。电商策划任务在激活后补充相同身份审计。

不增加向量检索、数据库迁移、第三方依赖，也不修改旧 Runtime 平台路径。

## 验证记录

- 后端定向验证去重共 **816 passed，6 skipped**（受影响模块 814 项，收尾共享初始化和缺项处理定向 199 项包含新增 2 项）。包含真实临时 PostgreSQL 的目录、作者审核发布、绑定、推荐、撤权、资源及计划任务回归；新增多模式审核/NAS 元数据一致性、固定项按模式隔离、失效固定项拒绝执行、媒体 Handler 和 HTTP 私有字段隔离。六项跳过是未启用 `SKILL_LIVE_EVAL` 的真实供应商验收。
- 前端：**165 passed，16 个测试文件**。涵盖四种媒体模式的空正文与独立 Skill 意图、旧聊天、附件、发送保护、模式切换与迟到结果、绑定写入竞态、推荐隔离、后台编辑、服务请求和幂等重试。
- TypeScript 和开启 Skill/推荐 UI 的正式构建通过。改动 TypeScript 文件 ESLint 无错误；保留 `InputArea` 两处既有 hook 依赖警告，以及原构建的大 chunk 提示。
- 先前沙箱内 PostgreSQL `initdb` 被限制，导致数据库用例初始化报错；随后在授权的隔离环境完整重跑上述模块，通过。所有数据库仅为临时目录中的 socket-only 实例，没有连接生产。
- 未调用真实图片/视频模型、未产生生产生成任务、未做浏览器生产验收。任务工作树保留供验收。
- 2026-10-01 首次受控发布候选 `5eb9d97c54f268a53e8ac9504c004b5b3dd18cd2`：前端全量 **1563 passed / 148 files**，构建及前端部署完成；后端全量 **10921 passed / 8 failed / 43 skipped / 4 xfailed**，因此未更新后端，完整发布失败、候选失效且锁保留。八项失败来自旧测试以无限制 MagicMock 模拟 HTTP 请求，新字段被造为假对象。两个测试文件的三个请求工厂改为真实 GenerateRequest，生产校验及原断言不变；消息路由、槽位释放、多模式和幂等入口定向复测 **79 passed**。修复后定向复测通过；用户明确“继续”授权恢复本次锁并重新完整发布。恢复前已复核锁所有者 `20261001074645-36745-29912-12248`，本地与远端部署进程均已退出、生产后端 active、候选不存在。后续发布结果以 release.sh 的结构化结果为准。

## 发布后的验证步骤

用户已于 2026-10-01 授权提交部署；使用 `deploy/release.sh` 发布当前任务，失败保留锁须先恢复再继续。不部署旧 UI 任务，不直接运行 `deploy/deploy.sh`。

1. 在测试组织通过现有草稿/审核/发布流程配置实际适用的媒体 Skill。例如“商品白底图”设为图生图，正文要求至少一张商品原图、保留主体结构并使用白色背景；不要直接改动现有提示词专用方法的用途。
2. 逐个切换智能、图生图、文生图、电商图、视频，检查 Skill 只列出对应方法，附件/输入标签/弹层位置和范围图标保持原样。
3. 图生图上传原图后选择方法，正文留空并生成；文生图/视频用资料充分的方法留空生成，再补充文字重试。核对模型、比例、数量和原有参数；核对任务审计中的 ID/固定版本以及生成提示词。
4. 电商图选择适用方法、上传商品图、留空正文；应先返回方案卡片，只有确认方案才产生出图调用。
5. 固定一个兼容多模式的方法，切换模式确认其生效范围；固定不兼容方法应在其他模式隐藏，切回后仍在。测试仅本条清除、四项会话容量和范围写入中切换模式。
6. 禁用 Skill、撤销组织授权、更换目录版本、制造方法缺项，确认失败不产生图片/视频 Provider 调用与积分锁定；无候选可继续原普通任务。推荐关闭不影响手动选择；目录关闭不读正文，显式选择拒绝；运行层关闭且有适用固定项时拒绝生成。

## 回滚点

发布前基座为 `260ed1bffb92896653542e41ffe4701ecc755c6e`；本次未改库结构、旧 Skill revision、旧审核记录或原输入布局。尚未发布任何带 `task_modes` 的版本时，可在当前任务形成仅回退本次差异的提交，再走受控发布。

一旦发布带新字段的 Skill revision，旧解析器的 `extra=forbid` 不能直接读取它。回退媒体行为时应保留新字段的兼容解析和模式隔离，撤回媒体入口/执行接入；不能直接部署基座使目录或绑定失效。先核对活跃任务与固定版本，不删除审核/绑定/审计历史，不重新部署未经验收的合并结果。

## 改动文件

- `"docs/document/TECH_Skill\345\244\232\346\250\241\345\274\217\346\216\245\345\205\245.md"`
- `"docs/document/TECH_Skill\350\201\212\345\244\251\351\200\211\346\213\251\344\270\216\345\217\215\351\246\210.md"`
- `"docs/document/UI_Skill\345\277\253\346\215\267\351\200\211\346\213\251.md"`
- `backend/api/routes/message.py`
- `backend/api/routes/skills.py`
- `backend/schemas/message.py`
- `backend/services/agent/image/image_agent.py`
- `backend/services/handlers/ecom_image_handler.py`
- `backend/services/handlers/image_handler.py`
- `backend/services/handlers/video_handler.py`
- `backend/services/message_idempotency_service.py`
- `backend/services/skills/available.py`
- `backend/services/skills/bindings.py`
- `backend/services/skills/contracts.py`
- `backend/services/skills/media.py`
- `backend/services/skills/recommendation_repository.py`
- `backend/services/skills/recommendation_service.py`
- `backend/services/skills/recommendations.py`
- `backend/services/skills/resolver.py`
- `backend/services/skills/runtime.py`
- `backend/services/skills/runtime_source.py`
- `backend/tests/test_skill_authoring_postgres.py`
- `backend/tests/test_skill_available_api.py`
- `backend/tests/test_skill_bindings_postgres.py`
- `backend/tests/test_skill_multimode.py`
- `backend/tests/test_skill_resolver.py`
- `frontend/src/components/admin/skills/SkillDraftEditor.tsx`
- `frontend/src/components/chat/input/InputArea.tsx`
- `frontend/src/components/chat/input/InputControls.tsx`
- `frontend/src/components/chat/input/InputControls.types.ts`
- `frontend/src/components/chat/input/SessionSkillBindings.tsx`
- `frontend/src/components/chat/input/SkillRecommendations.tsx`
- `frontend/src/components/chat/input/SkillSelector.tsx`
- `frontend/src/components/chat/input/__tests__/SessionSkillBindings.test.tsx`
- `frontend/src/components/chat/input/__tests__/SkillSelector.test.tsx`
- `frontend/src/components/chat/input/__tests__/useInputSubmission.test.tsx`
- `frontend/src/components/chat/input/useInputSubmission.ts`
- `frontend/src/components/chat/input/useSkillBindings.ts`
- `frontend/src/components/chat/input/useTurnSkillSelection.ts`
- `frontend/src/hooks/handlers/useMediaMessageHandler.ts`
- `frontend/src/services/__tests__/messageSenderRetry.test.ts`
- `frontend/src/services/__tests__/skillBindings.test.ts`
- `frontend/src/services/messageSendLifecycle.ts`
- `frontend/src/services/messageSender.ts`
- `frontend/src/services/skills.ts`


发布复测同步文件：

- `backend/tests/test_message_routes.py`
- `backend/tests/test_slot_leak_fixes.py`
