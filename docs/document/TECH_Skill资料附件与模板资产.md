# Skill 第二期第 2 步：资料附件与模板资产

本期在现有草稿审核、不可变 revision、受控存储和 Actor Turn 快照上增加文本资产及其上传原文件。不增加 MCP、模型工具、Skill 脚本执行、文件下载接口或数据库迁移，不修改旧 Runtime 平台路径。文件上传修正与验证见下文“直接上传文件”。

## revision 与审核契约

资产类型为 `reference`（只读资料）、`template`、`example_input`、`example_output`。模型读取的文本支持 UTF-8 的 `md/txt/json/csv`，拒绝 NUL 和脚本扩展名。最多 16 份，读取文本单份 64 KiB、总计 256 KiB；限制均按实际 UTF-8 字节数执行。可上传上述文本文件及 DOCX、PDF、XLSX，系统提取文档文字，同时保留原文件；原文件单份 2 MiB、总计 8 MiB。

管理 API 的 `DraftContent.assets` 每项接受 `id/name/kind/summary/format/content`，上传文件另有可选 `source: {format, base64}`。客户端不能指定服务器路径、哈希、revision 或模板值。草稿保存在现有 JSONB 内，保存仍生成新 revision 标识并递增版本。提交审核时由服务端生成清单与 SHA-256，审核批准绑定包含清单的整个 `SKILL.md` 哈希。改动附件内容、原文件、摘要、类型或变量声明都会改变批准哈希，必须重新审核。

最终文件为 `<现有所有权命名空间>/<skill_key>/<revision>/SKILL.md` 和同目录下 `assets/<id>.<format>`。上传原文件另存为 `assets/<id>.original.<source.format>`。清单置于 `SKILL.md` frontmatter 的 `assets`，包含 `id/name/kind/summary/format/path/sha256/bytes`；有原文件时另含 `source: {format,path,sha256,bytes}`。两类 `path` 都只能精确等于服务端规范名称，不能自由选取子目录。已有数据库不可变 `content_sha256` 锁定清单，清单再锁定提取文本及原文件的字节；因此无需另设可漂移的数据库副本或改历史迁移。

发布前验证清单与所有附件，再在同一 staging revision 写入正文和附件、设置文件只读、fsync，最后原子 rename 安装整个目录。已有完整 revision 不覆盖；相同内容的失败重试须重新校验全部文件。数据库提交失败或 rename 回执丢失保留完整孤立版本，仍可按原机制重试。未声明文件不会被加载或执行。

所有读取沿用 dir_fd、O_NOFOLLOW、普通文件与单硬链接检查；资产文件与中间目录都不能通过符号链接绕过根目录。每次实际附件读取都验证文本和原文件的字节数、SHA-256，文本另验 UTF-8。发布、管理员历史读取和解除停用核验全部附件；Actor 仅读取本次明确引用的附件，原文件只做完整性校验，不交给模型或工具。

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

管理员草稿页直接上传文件，自动填写名称、默认用途和唯一标识，默认显示紧凑文件列表；说明、类型与文字预览收在“查看内容与设置”。点击引用按钮才写入正文引用，移除附件同时清除该 ID 的引用。历史手工文本附件仍可在设置内编辑；上传文件的文字预览只读，修改原文件需重新上传。编辑区可插入显式类型化动态信息，高级设置保留手动管理入口。已发布及历史版本只返回 `asset_summaries`，不返回附件正文、原文件、路径或哈希；上传文件摘要增加 `file_format/file_bytes`。平台包转管理草稿时保留原资产与原文件，之后修改只影响新 revision。

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

### 编辑流程简化（2026-09-21，提示修复后的增量）

用户再次反馈难以使用。截图已显示新错误提示，但手写占位符和高级设置声明仍然分离；保存后审核失败还同时显示绿色保存成功和红色失败信息。根因是编辑器把内部配置步骤直接交给管理员，单独改善服务端错误提示不足以解决操作困难。

局部交互调整：

- 操作说明默认写普通文字，附件和动态信息均为可选。正文和模板附件提供“插入动态信息”菜单，在当前光标位置插入占位符并显式写入对应类型和白名单来源。
- 对已粘贴的正文和模板附件，正文上方列出尚未启用的已知信息，用户点击“启用这些信息”后补齐声明。加载页面不改数据，普通参考资料和示例不扫描变量；未知名称不会自动声明，保留既有自定义声明，不扩大来源或数量上限。
- 新附件自动生成标识并展开编辑；名称、用途、类型、内容直接可填。点击“在操作说明中引用”才写入明确引用，添加附件本身不加载附件内容。标识和格式放入“更多设置”，生成标识避开现有附件及正文残留引用，防止复用已移除附件的标识而意外绑定新内容。
- 修改草稿后清除过时提示；保存后审核失败只显示一条包含保存状态的失败说明。无效附件引用同时识别未声明附件与引用语法错误，指向附件上的引用按钮。

不修改后端审核、模板来源白名单、服务端类型、哈希、预算、资产存储或生命周期约束。不会自动修改用户生产草稿。增量发布仍需用户明确“提交部署”。

本次文件：`frontend/src/components/admin/SkillAdminPanel.tsx`、`skills/SkillDraftEditor.tsx`、`skills/SkillAssets.tsx`、新增 `skills/SkillTemplateInput.tsx` 与 `skills/templateEditing.ts`；对应 `__tests__/SkillAdminPanel.test.tsx`、`SkillAssets.test.tsx`、新增 `SkillTemplateInput.test.tsx`；本文。

验证：前端管理页、附件与动态信息编辑 **47 passed**；TypeScript 和改动文件 ESLint 通过。覆盖原截图草稿的一键启用并提交、光标连续插入、类型化声明、未知名称拒绝自动配置、参考资料保持字面文本、自定义映射保留、声明数量上限、附件自动标识及明确引用、提交失败后的提示一致性。本地浏览器使用无 API 的测试草稿实际检查一键启用、添加模板、插入会话信息和正文引用；没有执行生产业务验收。

生产验证：刷新编辑页后，这类草稿应直接显示“启用这些信息”，点击后再保存并提交审核；新建普通说明应无需变量配置。新增模板时填写名称、用途与内容，在菜单中选择需要的信息，点击“在操作说明中引用”，再走原审核发布流程。回滚点为已发布的提示修复提交 `c3617d95b8cce5b09ed0c59e77a4e6c1f3767c6c`；无数据迁移，新增草稿仍使用同一内容契约。

### 直接上传文件（2026-09-21，用户验收修正）

用户明确“附件就是我自己直接上传的”，并确认暂不需要图片。此增量修正此前把附件等同手工文本表单的理解；不自动修改生产草稿。

- 上传入口：`POST /skills/admin/orgs/{org_id}/attachments/import`，复用活跃组织、当前成员与管理员权限检查，响应 `no-store`。使用有界 multipart 文件读取；返回可审核的草稿资产，不写用户工作区、OSS 或公开 URL，不接受服务器路径。
- 支持 `.docx/.pdf/.xlsx/.txt/.md/.csv/.json`；拒绝旧 `.doc/.xls`、图片及脚本格式，并提示另存。文本使用 UTF-8（可带 BOM）；原字节完整保留。DOCX 提取正文与表格，PDF 提取可复制文字，XLSX 提取单元格值及字面公式，不运行宏、不计算公式、不保留模型中的原始版式；扫描 PDF 没有可读文字时明确失败，不做 OCR。
- 复用已安装的 `python-docx/openpyxl/pypdf`。文档解析在固定、一次性的库解析子进程内完成；每服务进程最多同时 2 份，单次墙钟 10 秒、CPU 5/6 秒、Linux 地址空间 512 MiB。不是 Skill 执行入口，不运行用户脚本、外部命令或网络读取。Office 压缩包限制 512 项、总展开 16 MiB、单项 8 MiB，拒绝越界条目、加密条目、宏/嵌入对象与实体声明。XLSX 最多 16 张表，每表 2000 行、100 列；PDF 最多 100 页，超限明确拒绝而非悄悄截断。
- 上传原文件在草稿 JSONB 的有界 base64 字段内保存，发布时与读取文本一起只读、原子落盘，审核哈希覆盖两份内容。渲染与旧任务恢复继续只加载正文明确引用且预算允许的文本，并校验对应原文件；未引用或超预算时两份内容都不读。单 Skill 24 KiB 的渲染预算保持不变，64 KiB 是资产存储上限，不代表一定能在一次激活中全量加载。
- 原文件字段为空时不会写入旧文本草稿；旧 revision frontmatter 和旧快照的资源摘要哈希保持不变。新建修订保留原上传文件，旧任务仍恢复原版本；原文件漂移同样拒绝恢复。
- 前端支持多选；原文件总量和读取文本总量都校验。批量读取全部成功才加入当前草稿，失败保留原编辑内容；上传过程中锁住保存、审核及导航，组织切换后的旧响应丢弃。默认不展开必填表单，文件名和用途已有默认值。

增量文件（相对本次前一生产版本）：

| 范围 | 文件 |
| --- | --- |
| 上传与资产 | `backend/api/routes/skill_admin.py`、`backend/services/skills/imports.py`（新增）、`assets.py`、`storage.py` |
| 审核与兼容 | `backend/services/skills/authoring.py`、`authoring_contracts.py`、`renderer.py` |
| 后端验证 | `backend/tests/test_skill_imports.py`（新增）、`test_skill_assets.py`、`test_skill_assets_postgres.py` |
| 前端服务与流程 | `frontend/src/services/skillAdmin.ts`、`frontend/src/components/admin/SkillAdminPanel.tsx` |
| 编辑组件 | `frontend/src/components/admin/skills/SkillAssets.tsx`、`SkillDraftEditor.tsx`、`SkillWorkspace.tsx`、`templateEditing.ts`、`attachmentUploads.ts`（新增） |
| 前端验证 | `frontend/src/components/admin/__tests__/SkillAssets.test.tsx`、`SkillAdminPanel.test.tsx`、`SkillTemplateInput.test.tsx` |
| 记录 | 本文 |

验证结果：后端 **265 passed**（导入、资产、运行快照、存储、审核 API、真实临时 PostgreSQL 审核发布/解除停用）；前端 **54 passed**，TypeScript `tsc -b` 和改动文件 ESLint 通过。新增覆盖真实七种格式导入、格式/编码/空文件/密码/容量错误、解析超时与并发容量释放、路径/压缩膨胀/宏/实体检查、原文件哈希和审核绑定、组织权限、摘要模式零文件读取、旧文本兼容、废弃后旧上传快照恢复及原文件漂移拒绝、前端上传锁定/组织切换/总量预算/删除引用。初次前端回归仅一处旧按钮名断言过期，更新后全部通过。

本地浏览器通过真实文件选择器上传合成 DOCX、PDF、XLSX 到仅监听本机的临时解析服务，三次实际导入接口均 200；核对紧凑文件列表、DOCX 正文和表格预览、引用写入及移除后引用清理。临时服务与预览文件已清理；未连接生产、未调用真实模型，未提交或部署本增量。

生产验证步骤（用户明确“提交部署”后）：

1. 刷新“附件验收测试”的草稿，点击“上传附件”，分别选一份小型 DOCX、可复制文字的 PDF、XLSX；应直接出现文件名与类型，无需手工填写名称、用途或正文。
2. 展开“查看内容与设置”核对读取文字；只给需要使用的文件点击“在操作说明中引用”，保存并走审核发布。聊天中显式选择该 Skill，核对附件中的独特测试标记。保持总引用文本较小，避免既有渲染预算影响这一步。
3. 上传图片、空文件或超容量文件应收到具体提示，原草稿不变。移除一份已引用附件后再次提交应不再因残留引用失败。
4. 为同一测试 Skill 发布带不同标记的新文件版本；原任务恢复仍使用旧标记。废弃后不可新激活，已有快照仍可恢复。路径穿越和哈希破坏只在本地或专用存储副本执行，不改真实历史资产。

本增量回滚点：`c481700b86729a241330b4eb28868473de574af7`。无数据库迁移；保留新草稿、原文件、revision 和 checkpoint。该旧代码不识别上传资产的 `source` 字段，若生产已创建上传资产，应先停止受影响 Skill 的新使用并处理其运行任务，再受控回退；含上传资产的草稿/版本不能在旧代码上继续管理或恢复。未增加原文件字段的旧文本 Skill 保持兼容。不能通过删除原文件、清单字段或历史数据实现回滚。

## 回滚点

任务基座：`e2d8a7d4f37205a247bcc090da2d399f43356c8a`。
任务分支：`codex/task/20260921195203-skill-revision-assets`。

优先关闭 `SKILL_RUNTIME_ENABLED`，阻止新激活并安全停止已有 Skill 快照恢复；保留草稿、清单、NAS 各版本与 checkpoint。若需代码退回基座，先完成或通过现有取消入口终止受影响 Turn、停用新增资产 Skill 并保存管理数据；旧实现不识别附件和服务端变量，不能继续运行这些 Skill。无需数据库回滚，不删除历史资产，不在本任务自行修改生产开关。
