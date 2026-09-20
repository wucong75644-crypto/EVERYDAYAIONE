# Skill 第二期第 1 步：草稿、审核与发布

任务基座 `a71b0d55f90e319125d1ca15bd5434179a4bf220`。只修改 Skill 控制面、Actor Skill 恢复适配和组织管理 UI，不改旧 Runtime 平台路径。不部署、不合并、不关闭工作树。

## 设计与任务地图

- 在 `skill_drafts` 保存每包唯一工作副本、乐观版本、每次保存由服务端生成的新候选 revision、审核者及审核哈希。状态为 draft → in_review → published；审核退回恢复 draft；published → draft 开始下一版；draft/in_review/published → deprecated；任意未禁用状态 → disabled。废弃/禁用后不再编辑或发布。
- 草稿与不可变 `skill_revisions` 分离。重新编辑不会停止当前发布版本；已发布正文、摘要、目录元数据、NAS 路径和双哈希继续由数据库不可变约束保护。旧目录中的包可按需建立管理副本，不扫描 NAS 自动导入。
- 所有组织管理 HTTP 请求使用登录用户和数据库当前 active 用户/组织/成员 owner/admin 身份。客户端 org_id 只是目标，不是授权；正文请求不能提供 scope、路径、发布哈希或审核人。平台包不通过组织端修改。个人创建/自动触发本轮均不开放。
- 审核冻结正文，单独记录通过或退回；编辑使审核失效。发布需要当前版本已审核；以行锁串行化和 expected_version 拒绝过期保存、重复发布和并发状态操作。
- 发布先在受控 NAS 的临时目录写入只读文件并 fsync，通过目录 rename 原子安装完整 revision，再重新读取并验证双哈希；成功后同一数据库事务 INSERT revision、切换本组织 assignment、更新草稿状态并记审计。NAS 失败不改变数据库。数据库失败可留下不可见的完整孤立文件；保存改稿会生成新 revision，避免被旧孤立文件阻塞；原内容重试只允许复用字节完全一致的文件，绝不覆盖或删除已发布文件。NAS 挂载需为发布进程提供专用目录写权限，不能与用户工作区重叠。
- revision 增加 deprecated/disabled，保留 retired 作为旧安全撤销状态。废弃将包所有 published revision 标为 deprecated，新目录与新激活均排除；已激活 checkpoint 恢复可以读取相同 deprecated revision，仍重验身份、assignment、业务权限、正文哈希和工具上限。disabled/retired、授权撤销、哈希异常继续阻断恢复。未激活的旧目录条目不能借恢复激活废弃版本。
- 增加只追加的控制审计，记录包创建、草稿编辑、提交/审核/退回/发布/废弃/禁用、revision 状态与 assignment 变化；与变更同事务提交，不存正文。每次 HTTP 请求生成独立 request_id；assignment 切换记录 from_revision_id/to_revision_id。

## 管理体验

沿用 `/admin` 的组织管理员入口和现有视觉令牌，增加 Skill 页签。列表按组织/平台分区，支持名称/标识搜索和状态筛选，展示工作状态与实际可用版本；平台 Skill 只读并默认显示正文。组织 Skill 可创建、编辑、保存、提交审核、审核通过/退回、发布、废弃、禁用。编辑区显示名称、说明、Markdown 正文和高级目录声明；审核时只读，发布按钮只在审核通过后出现。历史正文按需从受控 NAS 校验后读取，只读展示。发布、退回、废弃/停用使用确认弹窗及新旧任务影响说明；请求失败保留本地编辑内容，组织切换清空旧数据。

审核允许 owner/admin 对同一草稿分别执行审核和发布，审计记录实际身份，不强制双人分离。审核通过保留 in_review 状态并绑定 approved_by、approved_sha256、approved_at；编辑必须先退回 draft，所有旧审核信息失效。

## 实施与验证

依次完成迁移/状态约束、存储写入/管理服务、认证 API、运行恢复区分、组织 UI。使用临时 PostgreSQL 验证真实迁移、RLS、事务回滚、并发发布和审计；临时 NAS 验证失败、符号链接、幂等重试和篡改；Skill 定向回归验证旧 revision、废弃后的新旧 Turn、禁用及权限撤销；前端交互测试和 TypeScript/构建验证。

## HTTP 契约

`/api/skills/admin/orgs/{org_id}`：GET 列表、POST 创建（skill_key、content）。
`/{package_id}` GET 返回管理详情（草稿及只读历史摘要）；`/{package_id}/draft` PUT 保存（expected_version、content）；`/{package_id}/transitions` POST 状态动作（expected_version、action）；`/{package_id}/revisions/{revision}` GET 按哈希验证历史正文。

管理详情和正文只能由该目标组织的 active owner/admin 读取；平台发布正文对组织管理员只读，平台草稿不返回。路径中的组织是精确操作目标，服务端核验该组织成员身份，忽略 JWT org 和 X-Org-Id 的赋权含义。组织切换过程中旧请求不能落到另一个组织。个人无组织入口，不新增个人目录或自动触发。

请求不接受 scope、NAS 路径、revision 编号、审核人或哈希。409 表示过期 expected_version 或重复 skill_key，403/404 表示授权/可见性失败，422 表示状态或内容无效，503 表示服务/存储不可用。NAS 路径、数据库错误和哈希不返回客户端。审核中禁止保存；废弃/禁用均为终态，禁用还拒绝已激活恢复；需要新 Skill 时使用新的稳定标识。

## 生产验证与回滚

仅在用户明确“提交部署”后走 `deploy/release.sh`。检查新迁移账本、受控 NAS 专用目录写权限和目录与用户工作区隔离；用两个测试组织验证完整创建→保存→提交→审核→发布流程、跨组织/平台拒绝、重复发布、废弃后新 Turn 不可见而已激活 Actor 恢复旧版本、禁用拒绝恢复、NAS 只读/篡改失败。生产配置和真实 NAS 验证不由本地测试替代。

回滚代码到任务基座前关闭 Skill catalog/runtime 并完成或取消有 Skill checkpoint 的 Turn。保留数据库、审计、NAS 原版本；旧代码不认识新增 revision 状态，不能直接重开旧控制面。数据库回滚只允许新表无数据且不存在 deprecated/disabled revision，拒绝删除有效草稿和审计。NAS 文件不自动清理。

### 生产验收清单（本轮未执行）

1. 明确“提交部署”后，仅从当前任务工作树运行受控 `deploy/release.sh --message ... --file ...`。检查 259 迁移 checksum、两张新表的 FORCE RLS、不可变和审计触发器；不直接调用底层部署脚本。
2. 验证 `SKILL_STORAGE_ROOT` 的实际挂载、发布进程创建子目录/rename/fsync 权限、文件读权限，根目录与用户工作区互不包含。以专用测试 Skill 验证文件 mode、单链接、正文和双 SHA-256。不得对现有正式 revision 做故障注入。目录 rename 必须支持原子安装且拒绝替换非空目录；不能用覆盖写作为降级。
3. 组织 A 的 owner/admin 在“管理后台 → Skill 管理”创建草稿、保存、提交、通过、发布。检查当前 revision、组织 assignment 和同 request_id 的审计；再次编辑只改变草稿。用普通成员、组织 B 管理员和个人账号访问相同 URL 验证拒绝；组织管理员对平台包无编辑/审核/发布能力。
4. 两个管理员同时发布同一 expected_version：仅一次成功，另一次 409，只有一个 revision。测试组织中模拟 NAS 不可写和数据库提交失败：无可见新版本，草稿仍待发布；同内容恢复后可重试，退回编辑再审也可发布，已存在文件字节不变。
5. 激活 v1 后暂停 Actor，再发布 v2；旧 Actor 恢复仍读 v1，新的 Turn 选 v2。废弃包后新目录为空、先前未激活的目录条目不能激活；已激活 Actor 仍恢复原正文与工具上限。禁用、授权撤销和测试副本哈希漂移均应拒绝恢复。检查每次状态变化都有实际 actor、组织、时间和前后状态。
6. 回归现有聊天的普通文本、手动 Skill 选择、工具权限、暂停/继续，确认个人会话仍无组织 Skill 自动触发。

### 回滚点

代码基座为 `a71b0d55f90e319125d1ca15bd5434179a4bf220`。优先在必要时关闭 Skill 开关并保留草稿、审计、NAS 版本。代码回退前处理完所有含 Skill checkpoint 的未结束 Turn；旧代码不认识 deprecated/disabled，保留新数据时不能直接重新开启旧控制面。`259_skill_authoring_rollback.sql` 只供受控回滚，在单个事务锁表并检查完整数据后执行；任何新草稿、审计或新增状态存在即拒绝。失败回滚恢复 FORCE RLS。NAS 完整孤立版本和崩溃遗留 staging 目录不自动删除，避免误伤有效 revision。

## 最终验证记录（2026-09-20，本地）

- Skill 全链路回归：281 passed、0 skipped（修复前完整组，含已有存储/解析/权限/手动选择/Actor 恢复和数据库测试）。独立审查后对受影响的新控制面、NAS 和 HTTP 模块复测并扩大边界：70 passed、0 skipped。两组去重共 305 项，最终修复涉及的测试已重新通过。
- PostgreSQL 17 只在临时目录以 Unix socket 启动；未使用项目 DATABASE_URL。沙箱内首次 initdb 受限，获准使用本地临时实例后通过，未跳过数据库验证。NAS 使用临时目录模拟，未连接真实生产挂载。Python 使用现有项目虚拟环境和已有临时 PyYAML 6.0.2 依赖目录，未修改依赖声明。
- 前端管理流程及原聊天 Skill 选择：22 passed（管理 7、选择 11、输入控件 4）；最后一次组织切换清理修正后管理 7 项再次通过。TypeScript、生产构建、改动文件 ESLint 通过；构建保留现有大 chunk 提示，原选择组件测试存在既有 AnimatePresence act 警告。
- 独立后端审查发现并修复失败发布后改稿被孤立文件阻塞，以及旧硬链接安装的进程中断窗口；最终只读复查无新增高置信缺陷。补测 NAS rename 已成功但回执丢失、临时目录清理失败和 staging 遗留后的恢复。
- `git diff --check` 通过。未部署、未推送、未合并 main、未清理工作树；旧 Runtime 平台路径未修改。

## 改动文件清单

| 文件 | 职责 |
| --- | --- |
| [backend/api/routes/skill_admin.py](../../backend/api/routes/skill_admin.py) | 组织管理员认证与管理 HTTP 契约 |
| [backend/main.py](../../backend/main.py) | 注册管理 API |
| [backend/migrations/259_skill_authoring.sql](../../backend/migrations/259_skill_authoring.sql) | 草稿状态、审核约束、RLS 与追加审计 |
| [backend/migrations/rollback/259_skill_authoring_rollback.sql](../../backend/migrations/rollback/259_skill_authoring_rollback.sql) | 保留数据的受控数据库回滚门禁 |
| [backend/services/skills/authoring.py](../../backend/services/skills/authoring.py) | 草稿工作流与事务发布 |
| [backend/services/skills/authoring_contracts.py](../../backend/services/skills/authoring_contracts.py) | 严格管理输入与审核文档生成 |
| [backend/services/skills/contracts.py](../../backend/services/skills/contracts.py) | 废弃/禁用 revision 契约 |
| [backend/services/skills/repository.py](../../backend/services/skills/repository.py) | 新激活与旧恢复的 revision 状态区分 |
| [backend/services/skills/runtime.py](../../backend/services/skills/runtime.py) | 只在已激活 checkpoint 恢复时放行废弃版本 |
| [backend/services/skills/runtime_source.py](../../backend/services/skills/runtime_source.py) | 只在已激活 checkpoint 恢复时放行废弃版本 |
| [backend/services/skills/storage.py](../../backend/services/skills/storage.py) | 受控 NAS 原子发布和读回校验 |
| [backend/tests/test_skill_authoring_api.py](../../backend/tests/test_skill_authoring_api.py) | 状态、权限、存储、并发或交互定向回归 |
| [backend/tests/test_skill_authoring_postgres.py](../../backend/tests/test_skill_authoring_postgres.py) | 状态、权限、存储、并发或交互定向回归 |
| [backend/tests/test_skill_runtime.py](../../backend/tests/test_skill_runtime.py) | 状态、权限、存储、并发或交互定向回归 |
| [backend/tests/test_skill_storage_publish.py](../../backend/tests/test_skill_storage_publish.py) | 状态、权限、存储、并发或交互定向回归 |
| [docs/document/TECH_ActorSkillRuntime.md](../../docs/document/TECH_ActorSkillRuntime.md) | 设计、兼容边界、测试或生产验证说明 |
| [docs/document/TECH_SkillCatalog与可见目录.md](../../docs/document/TECH_SkillCatalog与可见目录.md) | 设计、兼容边界、测试或生产验证说明 |
| [docs/document/TECH_Skill控制面与受控存储.md](../../docs/document/TECH_Skill控制面与受控存储.md) | 设计、兼容边界、测试或生产验证说明 |
| [docs/document/TECH_Skill草稿审核与发布.md](../../docs/document/TECH_Skill草稿审核与发布.md) | 设计、兼容边界、测试或生产验证说明 |
| [frontend/src/components/admin/AdminPanel.tsx](../../frontend/src/components/admin/AdminPanel.tsx) | 组织管理员 Skill 入口 |
| [frontend/src/components/admin/SkillAdminPanel.tsx](../../frontend/src/components/admin/SkillAdminPanel.tsx) | 列表、草稿、审核、发布、历史与废弃 UI |
| [frontend/src/components/admin/__tests__/SkillAdminPanel.test.tsx](../../frontend/src/components/admin/__tests__/SkillAdminPanel.test.tsx) | 状态、权限、存储、并发或交互定向回归 |
| [frontend/src/services/skillAdmin.ts](../../frontend/src/services/skillAdmin.ts) | 固定目标组织的管理 API 客户端 |


## 2026-09-20 UI 优化补充

用户确认 Skill 库 → 内容详情 → 专注编辑方案后完成实际组件改造，并随 `388fc6d0` 部署。详见 [UI_Skill管理体验优化](UI_Skill管理体验优化.md)，包括完整文件清单、错误与离开保护、定向验证、生产步骤及回滚点。该 UI 提交没有改变发布事务、状态机或 Actor 恢复边界。

## 组织发布 NAS 配置（2026-09-20）

用户明确授权组织 Skill 子目录的持久可写挂载、当前任务部署以及“测试日报”的发布验证。沿用已有同一 NAS，未新建 NAS 服务，也未修改 `FILE_WORKSPACE_ROOT` 或用户工作区挂载。

- `/mnt/nas-workspace` 保持原有读写挂载和用户隔离逻辑。
- `/mnt/platform-skills` 保持只读，已有平台 Skill 文件内容未变。
- 新建 NAS 的 `/.platform-skills/org` 目录（root:root、0750），仅将其读写挂载到 `/mnt/platform-skills/org`；沿用父挂载的其他选项。
- `/etc/fstab` 已添加该子目录挂载；systemd 的父挂载 Requires/After 依赖已验证。
- 后端已有 `50-skill-storage.conf` 通过 `ReadOnlyPaths=/mnt/platform-skills` 将子挂载也设为只读，因此主机 NAS 探针不能代替后端进程验证。首次真实发布返回 503，数据库仍为审核通过、revision 数量为 0，未写入发布文件；主机与后端 `/proc/<pid>/mountinfo` 分别显示组织子挂载 rw/ro，确定是服务命名空间的写入限制。
- 在后端新增 `skill-authoring-write.conf`（版本化模板：`deploy/everydayai-backend-skill-authoring.conf`），仅追加 `ReadWritePaths=/mnt/platform-skills/org` 与 `RequiresMountsFor=/mnt/platform-skills/org`。原 `ReadOnlyPaths` 和 `InaccessiblePaths=/mnt/nas-workspace/.platform-skills` 保留，Actor 服务配置不变。该例外使用 systemd 支持的只读目录内可写子目录机制；参见 [systemd 执行沙箱文档](https://github.com/systemd/systemd/blob/main/man/systemd.exec.xml)。安装后由受控应用部署重启后端；验收须检查后端实际挂载和真实 HTTP 发布。
- 真实 NAS 隔离探针验证了完整 revision 原子安装、只读文件、单链接、双哈希校验、相同内容重试保持 inode，以及冲突内容拒绝覆盖。探针未登记业务数据库，临时测试命名空间已清理。业务 Skill 的实际发布由部署后的已授权验收另行核对。

操作使用生产发布锁；配置变更前使旧验收候选失效，完整应用部署成功后由 `deploy/release.sh` 建立新候选。首次检查受到 NFS 新目录缓存延迟影响并安全停止，未写 fstab；确认两个挂载的目录 inode 一致后完成配置。

基础设施回滚参考：`/etc/fstab.skill-authoring-388fc6d0.bak`。需要回滚时核对现有 fstab，只移除本任务新增的组织子目录条目并卸载该子挂载、重新加载 systemd；保留 NAS 文件及其他挂载，不用旧备份覆盖后续无关配置。应用代码回滚参考 `388fc6d0`，通过受控发布流程执行。

后端写权限例外回滚：仅移除新增的 `/etc/systemd/system/everydayai-backend.service.d/skill-authoring-write.conf`，保留原 `50-skill-storage.conf` 等配置，经 daemon-reload 与受控发布重启后端恢复服务只读限制。已发布的 NAS 文件、数据库 revision 和审计均保留。
