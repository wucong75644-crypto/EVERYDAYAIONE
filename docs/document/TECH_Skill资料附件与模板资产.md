# Skill 第二期第 2 步：资料附件与模板资产

本期在现有草稿审核、不可变 revision、受控存储和 Actor Turn 快照上增加文本资产。不增加 MCP、工具、执行器、脚本执行、文件下载接口或数据库迁移，不修改旧 Runtime 平台路径。

## revision 与审核契约

资产类型为 `reference`（只读资料）、`template`、`example_input`、`example_output`。支持 UTF-8 的 `md/txt/json/csv`，拒绝 NUL、二进制格式和脚本扩展名。最多 16 份，单份 64 KiB、总计 256 KiB；限制均按实际 UTF-8 字节数执行。

管理 API 的 `DraftContent.assets` 每项只接受 `id/name/kind/summary/format/content`。客户端不能指定服务器路径、哈希、revision 或模板值。草稿保存在现有 JSONB 内，保存仍生成新 revision 标识并递增版本。提交审核时由服务端生成清单与 SHA-256，审核批准绑定包含清单的整个 `SKILL.md` 哈希。改动附件内容、摘要、类型或变量声明都会改变批准哈希，必须重新审核。

最终文件为 `<现有所有权命名空间>/<skill_key>/<revision>/SKILL.md` 和同目录下 `assets/<id>.<format>`。清单置于 `SKILL.md` frontmatter 的 `assets`，包含 `id/name/kind/summary/format/path/sha256/bytes`。`path` 只能精确等于服务端规范名称，不能自由选取子目录。已有数据库不可变 `content_sha256` 锁定清单，清单再锁定每个附件原始字节；因此无需另设可漂移的数据库副本或改历史迁移。

发布前验证清单与所有附件，再在同一 staging revision 写入正文和附件、设置文件只读、fsync，最后原子 rename 安装整个目录。已有完整 revision 不覆盖；相同内容的失败重试须重新校验全部文件。数据库提交失败或 rename 回执丢失保留完整孤立版本，仍可按原机制重试。未声明文件不会被加载或执行。

所有读取沿用 dir_fd、O_NOFOLLOW、普通文件与单硬链接检查；资产文件与中间目录都不能通过符号链接绕过根目录。每次实际附件读取都验证字节数、SHA-256 和 UTF-8。发布、管理员历史读取和解除停用核验全部附件；Actor 仅读取本次明确引用的附件。

## 模型可见内容与预算

未激活目录沿用既有摘要白名单，不读正文或附件。激活后默认注入正文和附件摘要（ID、名称、类型、用途、格式、字节数），不提供存储路径或哈希。

正文必须用 `[[asset:report-template]]` 明确引用已声明 ID 才会加载其内容。重复 ID 只读一次；非法/未声明 ID 拒绝激活。引用集合只从原始正文取得，模板替换值、附件内容或模型参数都不能引入额外读取。附件中的链接和其他附件引用仅作为普通文本，不跟随、下载或递归包含。

读取前检查正文、附件摘要、内容标题和所引附件原始字节是否能放入剩余预算：单 Skill 24 KiB、Turn 48 KiB、最多 4 个 Skill，正文原始 16 KiB 上限不变。超限整项拒绝，不读附件、不截断、不激活部分状态；模板替换后再次检查实际输出预算。预算使用 UTF-8 字节作保守 token 上界，不依赖模型供应商。未引用附件即使很大，也只注入摘要。

## 显式服务端模板变量

保留字面语法 `{{args.<name>}}`，但变量必须在 revision 的 `template_variables` 声明 `type/source`，实际值只能由服务器当前 ToolContext 提供。新激活不再接受模型提供的非空 `args`；`activate_skill` 广告只接受 `skill_id`。没有变量的既有 Skill 无需改动；依赖旧模型 args 的 Skill 须发布显式声明的新版本。旧的无资产 checkpoint 可按既有精确快照恢复，不重新解释参数。

| source | type | 来源 |
| --- | --- | --- |
| actor_user_id | string | 当前已验证成员 |
| org_id | string | 当前已验证组织 |
| conversation_scope | string | user/channel |
| agent_domain | string | general/erp |
| execution_mode | string | interactive/scheduled/preflight |
| is_channel | boolean | 当前范围是否为 channel |

仅有这些 source，不能指定环境变量、文件路径、密钥或任意对象属性。来源与类型必须匹配，布尔值不能由字符串转换；缺少服务端值拒绝激活。变量最多 16 个，规范化 JSON 4 KiB。替换只发生在正文与 `template` 附件，参考资料和示例保持字面原文。无模板表达式求值、文件插值或递归展开。

例如管理员保存以下 content，路径与哈希均由服务端生成：

```json
{
  "description": "按固定格式整理当前组织的报告",
  "body": "遵守当前权限，参考 [[asset:report-template]] 输出报告。",
  "catalog_metadata": {"model_selectable": false},
  "template_variables": {"org_id": {"type": "string", "source": "org_id"}},
  "assets": [{
    "id": "report-template", "name": "组织报告模板", "kind": "template",
    "summary": "规定报告标题与结论格式", "format": "md",
    "content": "# 组织 {{args.org_id}}\n\n## 结论\n依据当前已授权数据填写。"
  }]
}
```

## 快照、状态与 UI

ActiveSkill 增加 `asset_manifest_sha256`（完整资源声明的规范 JSON 摘要）和 `loaded_asset_ids`，继续保留精确渲染正文及其哈希。快照不保存服务器路径或额外附件原始内容副本。恢复先检查渲染预算，然后重新验证身份、当前授权、原 revision 正文/清单和已加载附件哈希，使用原渲染文本，不以新上下文重新填模板。

assignment 切到 v2 后，v1 Turn 仍恢复 v1 的附件。废弃 revision 不能新激活，但已激活快照可恢复；disabled/retired、撤销授权、原附件丢失或漂移都停止恢复，不查最新目录、不回退到新版。解除停用继续核验所有受影响历史版本，现在也包含全部附件哈希。

管理员草稿页可添加、编辑、移除文本附件，随原审核流程发布；高级设置显式选择有类型的服务端变量。已发布及历史版本显示附件摘要，接口只返回 `asset_summaries`，不返回附件正文、路径或哈希。平台包转管理草稿时保留原资产内容，之后修改只影响新 revision。模板配置错误及附件超预算以安全提示反馈，不回显底层路径。

## 定向验证

测试入口：`test_skill_assets.py`、`test_skill_assets_runtime.py`、`test_skill_assets_postgres.py`、已有 Skill 存储/Actor/管理/RLS 回归，以及前端 `SkillAssets.test.tsx`、`SkillAdminPanel.test.tsx`。

验证覆盖路径穿越及编码变体、符号链接/硬链接/FIFO、文件缺失/哈希漂移、未声明读取、摘要模式零附件 IO、单项/Turn/替换后预算、服务端严格类型、非法变量、原子发布失败和重试、取消、重复激活、压缩恢复、废弃/停用/退役、旧资产快照和解除停用历史核验。真实 PostgreSQL 测试仅启动临时 Unix socket 实例，不连接现有数据库。

### 首次发布前本地结果（2026-09-21）

- 后端 **597 passed、0 skipped**：全部 `tests/test_skill*.py`，以及 `test_chat_execution_engine.py`、`test_chat_generation_executor.py`、`test_tool_production_integration.py`、`test_replay_checkpoint_store.py`。包含 PostgreSQL 17 真实 RLS/不可变约束/授权和临时受控存储验证。
- 前端 **51 passed**：附件 3、管理 33、聊天 Skill 选择 11、输入控件 4。TypeScript `tsc -b`、全部改动前端文件 ESLint 与 `git diff --check` 通过。聊天选择器保留已有 AnimatePresence `act` 提示；没有新增测试失败。
- 第一轮新增 PostgreSQL 用例曾误判异常类型及允许的状态迁移，已按实际不可变 `CheckViolation` 与终态规则修正。管理 UI 测试曾在 Radix 弹窗关闭动画结束前查询页面按钮，现明确等待弹窗消失后保留全部原断言；未改变生产交互逻辑。
- 未调用真实模型，未连接生产数据库或 NAS，未执行生产验证、推送、部署、合并或清理工作树。

## 改动文件清单

| 范围 | 文件 |
| --- | --- |
| 资产契约与存储 | `backend/services/skills/assets.py`、`contracts.py`、`storage.py` |
| 审核与发布 | `backend/services/skills/authoring_contracts.py`、`authoring.py`、`catalog.py` |
| 加载、渲染与快照 | `backend/services/skills/renderer.py`、`runtime.py`、`runtime_source.py`、`feedback.py` |
| 后端新测试 | `backend/tests/test_skill_assets.py`、`test_skill_assets_runtime.py`、`test_skill_assets_postgres.py` |
| 后端受影响回归 | `backend/tests/test_skill_runtime.py`、`test_skill_runtime_actor.py`、`test_skill_manual_selection.py` |
| 前端接口 | `frontend/src/services/skillAdmin.ts` |
| 管理 UI | `frontend/src/components/admin/SkillAdminPanel.tsx`、`skills/SkillAssets.tsx`、`skills/SkillDraftEditor.tsx`、`skills/SkillWorkspace.tsx` |
| 前端测试 | `frontend/src/components/admin/__tests__/SkillAssets.test.tsx`、`SkillAdminPanel.test.tsx` |
| 文档 | 本文、`TECH_ActorSkillRuntime.md`、`TECH_Skill控制面与受控存储.md`、`TECH_Skill草稿审核与发布.md` |

## 生产验证步骤

1. 用户明确“提交部署”后，从当前任务工作树执行受控 `deploy/release.sh --message ... --file ...`；本期没有迁移，不合并或清理。
2. 在专用测试组织创建 Skill，添加四类附件和一个 `org_id` 模板变量，提交、审核、发布；检查当前/历史版本只显示摘要，文件随 revision 一起落地并只读，实际 NAS 支持原子目录 rename。
3. 激活只含摘要的版本，确认请求无附件正文；新版本正文只引用其中两份，确认模型上下文只包含这两份，变量来自当前服务端上下文。传入非空模型 args 应被拒绝。
4. 用专用大附件验证超预算拒绝；仅在专用测试包副本中验证越界路径、链接、附件缺失及哈希漂移，确认失败不激活且错误不含服务器路径。恢复测试副本原始字节后再验证。
5. 激活 v1 后暂停，发布并分配 v2；恢复仍是 v1 的原模板输出与附件。废弃后新 Turn 不可用而旧 Turn 可恢复；停用、退役或撤销授权停止恢复。确认原聊天工具权限和 Skill 控制屏障不变。

### 审核失败排查与提示修复（2026-09-21）

首次任务发布提交为 `584548998115d8f067413257f18e07624808c534`，生产发布标记和前端资源已只读核对。用户测试草稿 `asset-test-0921` 在正文中使用 `conversation_scope` 和 `is_channel`，但 `template_variables` 为空、附件列表为空；在生产只读事务中读取该草稿并调用纯校验函数，复现 `SKILL_TEMPLATE_VARIABLE_UNDECLARED`。没有修改草稿、执行审核或调用模型。

恢复操作：刷新页面，在“高级设置 → 服务端模板变量”勾选“会话范围”和“是否群组会话”，保存并重新提交审核。若验收附件功能，应将三行模板放入 ID 为 `demo-template` 的模板附件，在操作说明中明确引用 `[[asset:demo-template]]`。正文直接使用变量也是合法功能，但不能验证附件加载。

原审核接口只返回 FastAPI `detail`，共享前端错误解析器只读取 `error`，导致具体错误码丢失。增量修复在 Skill 管理接口的 422 业务响应中保留 `detail` 并补充标准 `error`；前端仅对白名单中的未声明变量、非法模板语法、无效附件引用显示针对性修正提示，未知错误仍使用通用提示，不展示服务端原文。审核规则与生命周期行为保持不变。

增量文件：`backend/api/routes/skill_admin.py`、`backend/tests/test_skill_authoring_api.py`、`frontend/src/components/admin/SkillAdminPanel.tsx`、`frontend/src/components/admin/__tests__/SkillAdminPanel.test.tsx`、本文。增量修复的发布状态以受控入口的 `RELEASE_RESULT` 和生产发布标记为准；回滚点为上述首次发布提交，无数据迁移。发布后验收时，先不勾选变量提交，确认出现明确提示且草稿保留；再勾选对应变量，确认保存并提交成功。

增量验证：后端审核接口与资产校验 **81 passed**；前端管理页和附件编辑器 **41 passed**，包含真实错误解析格式、失败后保留正文、勾选类型化变量并使用保存后版本重提审核的回归。TypeScript `tsc -b` 与改动前端文件 ESLint 通过。

## 回滚点

任务基座：`e2d8a7d4f37205a247bcc090da2d399f43356c8a`。
任务分支：`codex/task/20260921195203-skill-revision-assets`。

优先关闭 `SKILL_RUNTIME_ENABLED`，阻止新激活并安全停止已有 Skill 快照恢复；保留草稿、清单、NAS 各版本与 checkpoint。若需代码退回基座，先完成或通过现有取消入口终止受影响 Turn、停用新增资产 Skill 并保存管理数据；旧实现不识别附件和服务端变量，不能继续运行这些 Skill。无需数据库回滚，不删除历史资产，不在本任务自行修改生产开关。
