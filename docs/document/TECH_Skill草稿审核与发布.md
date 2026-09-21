# Skill 第二期第 1 步：草稿、审核与发布

任务基座 `a71b0d55f90e319125d1ca15bd5434179a4bf220`。只修改 Skill 控制面、Actor Skill 恢复适配和组织管理 UI，不改旧 Runtime 平台路径。不部署、不合并、不关闭工作树。

## 设计与任务地图

- 在 `skill_drafts` 保存每包唯一工作副本、乐观版本、每次保存由服务端生成的新候选 revision、审核者及审核哈希。状态为 draft → in_review → published；审核退回恢复 draft；published → draft 开始下一版；draft/in_review/published → deprecated；任意未禁用状态 → disabled。废弃后不再编辑或发布；停用期间不能编辑或发布，新增 `enable` 可恢复审计记录中的停用前状态（见下方增量修复，尚未部署）。
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

请求不接受 scope、NAS 路径、revision 编号、审核人或哈希。409 表示过期 expected_version 或重复 skill_key，403/404 表示授权/可见性失败，422 表示状态或内容无效，503 表示服务/存储不可用。NAS 路径、数据库错误和哈希不返回客户端。审核中禁止保存；废弃为终态，停用还拒绝已激活恢复；重新启用仅撤销本次停用，不接受客户端指定目标状态，不产生新 revision。

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

## 停用后重新启用修复（2026-09-20，未部署）

用户在 `c7e41279` 发布成功后测试停用，发现无法恢复。根因为原设计把 disabled 设为终态：前端不展示恢复入口，HTTP 动作枚举无 enable，259 数据库触发器也拒绝逆向状态；旧测试只验证禁用后拒绝操作，没有覆盖临时停用后恢复这一使用需求。修复前在临时 PostgreSQL 中复现 enable 被动作契约拒绝。

- 已停用的组织 Skill 在详情页显示主按钮“重新启用”，确认框解释会恢复停用前状态。已有生产停用记录可以直接使用，无需重建 Skill 或重新发布正文。未提供管理副本的旧包仍按既有 disable 行为创建草稿，恢复到其审计记录中的 draft 状态，原已发布版本单独恢复。
- 增加 `260_skill_reenable.sql`，不改已部署 259 的内容或校验和。借助不可修改的审计中 package、草稿 version、disable 动作、request_id 和事务时间，精确识别本次停用前的 draft 状态及受影响 revision。状态改变仍要求组织 owner/admin、包归属校验、包锁和 expected_version；所有 enable 状态变化由既有触发器记入同一事务审计。
- 恢复草稿/审核中的内容与审核记录，不发布未审核内容；恢复原 published/deprecated revision，不修改正文、路径、哈希、revision 或版本数量。此前已废弃、retired、单独 disabled 的历史版本不会因本次恢复变成 published；assignment 的目标、开关和权限均保持原样，撤销授权不会自动恢复。
- 对本次恢复涉及的全部历史 revision 先从 NAS 读回校验双哈希，再原子恢复状态。缺失、篡改或数据库失败均保持停用，允许修复后重试。纯未发布草稿没有 NAS 文件，不依赖 NAS 初始化。Actor 仍按原 revision 和权限恢复；恢复到 deprecated 后新 Turn 仍被阻止。
- 定向验证：后端 authoring/API/恢复/存储共 137 项通过，含新增真实 PostgreSQL 恢复组 18 项；前端管理和离开保护 24 项通过；TypeScript、定向 ESLint、diff 空白检查通过。独立只读审查未发现阻塞问题，已采纳纯草稿延迟初始化 NAS 的建议。测试只使用隔离数据库与临时目录，未改变生产停用状态。

本次改动文件：

| 范围 | 文件 |
| --- | --- |
| 恢复服务与契约 | `backend/services/skills/authoring.py`、`authoring_contracts.py`、`reenable.py` |
| 迁移与回滚 | `backend/migrations/260_skill_reenable.sql`、`backend/migrations/rollback/260_skill_reenable_rollback.sql` |
| 按用户要求复用测试 | `deploy/release.sh`、`scripts/testing/test_release_acceptance_lifecycle.sh` |
| 页面与客户端 | `frontend/src/components/admin/SkillAdminPanel.tsx`、`frontend/src/components/admin/skills/SkillWorkspace.tsx`、`frontend/src/services/skillAdmin.ts` |
| 回归测试 | `backend/tests/test_skill_reenable_postgres.py`、`test_skill_authoring_postgres.py`、`test_skill_authoring_api.py`、`frontend/src/components/admin/__tests__/SkillAdminPanel.test.tsx` |
| 文档 | 本文、`docs/document/UI_Skill管理体验优化.md`、`docs/CURRENT_ISSUES.md` |

生产复验：明确“提交部署”后检查 260 迁移账本；进入“组织 Skill → 已停用条目”，点击“重新启用 → 确认启用”，核对停用前状态、原可用版本、版本数和正文不变。用新 Turn 验证原发布版本重新可用，并按原场景验证已激活 Turn 的固定版本恢复。若启用失败，状态仍应为已停用。不得为此重新创建或覆盖用户的 Skill。

回滚点为 `c7e41279523886c9abd3fecaff02733af95ceacc`。应用通过受控流程撤销本次增量；数据库配套 `rollback/260_skill_reenable_rollback.sql` 仅恢复旧 guard 并移除新辅助函数，不删除草稿、审计或 NAS 文件，也不擅自重新停用已恢复记录。当前未提交、未部署，未合并 main 或清理工作树。

2026-09-21 用户明确要求快速提交部署、仅做必要测试。复用上述 137 项后端、24 项前端及类型/静态检查；为受控发布入口增加显式 `--skip-test`，转发执行器已有选项，默认行为不变，仍执行前后端构建、迁移账本、发布锁、来源与 readiness 检查。如果发布前合入 main 改变候选，则停止复用，须对新候选补充必要验证后再发布。新增发布参数用临时 Git/模拟 SSH 的生命周期测试验证，不访问生产。


## 停用直接废弃与安全删除（2026-09-21，未部署）

### 已确认行为

用户明确要求核对后一次修改，选择 B：disabled 可直接 deprecate；deprecated 不再提供停用/启用/编辑/发布，只读查看及安全删除。“开始修改吧”按推荐删除范围授权：从管理列表移除，后台保留已发布 revision、NAS 正文、授权记录与审计；唯一标识不复用，不增加物理清除或恢复删除入口。上一版停用恢复 `254410e7` 已部署，本节是其后续增量。

### 状态、存储与删除保护

- disabled → deprecated 复用本次停用审计关联；仅将这一停用事务影响的 published/deprecated revision 转 deprecated。单独 disabled、retired 的旧版本及撤销的授权不重开。所有受影响 NAS 文件先验证双哈希，任一个失败均回滚。纯草稿无文件时不初始化 NAS。与解除停用一样使用 expected_version 和同包串行锁；新 Turn 禁止，原已激活 Actor 可按旧 revision 恢复。
- deprecated 禁止 disable，保留以前已处于 disabled-from-deprecated 的恢复兼容。已部署 259/260 不修改；新建迁移 261 扩展 guard。
- 在 skill_drafts 增加 deleted_at/deleted_by，生命周期 status 保持 deprecated。删除版本号增加并写 to_state=deleted 审计；正文与审批内容不变。管理列表/详情/历史读取入口排除删除标记，原 package/key/revision/NAS/assignment/audits 全部保留。
- 删除预检和实际删除均要求当前活跃组织管理员与包所有权。GET deletion-check 返回 allowed/reason/计数；DELETE 包入口只接受 expected_version，不接受 force/路径/身份。只有 deprecated 可删除。
- 检查当前组织未结束 chat tasks：手动 `_selected_skill`、checkpoint directory.package_id、active.skill_key，兼容 state.payload 包装。运行或暂停且缺少完整 Skill 快照、未知/损坏结构、JSON 字符串 request_params 均标记 uncertain 并阻止删除。终态任务即使保留 ready checkpoint 也不阻止删除；新 pending 无选择无快照不属于已建立的引用。
- 检查函数 SECURITY INVOKER + row_security=off，仅查显式当前 org 的 tasks/checkpoints，避免 RLS 隐藏记录后误判零依赖。没有新增读取其他组织数据的权限。缺表、权限/RLS、数据库异常和超时均失败关闭，前端禁用删除并允许重试。
- 实际删除依次取得同包 advisory 锁、draft 行锁，再对 tasks 与 conversation_turn_checkpoints 取得短暂 SHARE 锁，保证引用写入/恢复/排队不能跨越最终检查。锁等待 2 秒、服务内单语句 3 秒超时，失败回滚。预检不拿全局表锁。数据库删除 trigger 也再次检查，不能绕过 HTTP 的预检直接标记删除。
- 所有 revision/assignment 写入在 trigger 内锁 draft 并检查删除标记。repository 与 authoring 写入统一先包锁，再 draft/版本行，关闭删除与内部写入并发窗口；直接 SQL 的反序锁最多产生可重试数据库错误，不能绕过墓碑保护。runtime 读取原版的路径保持兼容；没有修改旧 Runtime 平台路径或 Actor 执行文件。

### 文件范围

| 职责 | 文件 |
| --- | --- |
| 状态与删除服务 | `backend/services/skills/authoring.py`、`reenable.py`、新增 `deletion.py`；`repository.py` 统一写锁 |
| 管理接口 | `backend/api/routes/skill_admin.py` |
| 迁移/回滚 | 新增 `backend/migrations/261_skill_safe_removal.sql`、`backend/migrations/rollback/261_skill_safe_removal_rollback.sql` |
| UI/客户端 | `frontend/src/components/admin/SkillAdminPanel.tsx`、`skills/SkillWorkspace.tsx`、新增 `skills/SkillDeletion.tsx`、`frontend/src/services/skillAdmin.ts` |
| 测试 | 新增 `backend/tests/test_skill_removal_postgres.py`；更新 authoring API/PostgreSQL、reenable PostgreSQL 与 `frontend/src/components/admin/__tests__/SkillAdminPanel.test.tsx` |
| 记录 | 本文、`UI_Skill管理体验优化.md`、`docs/CURRENT_ISSUES.md` |

### 验证与生产复验

定向验证（非全量）：Skill removal / authoring / reenable / catalog PostgreSQL 及 authoring API；界面 32 项与导航 2 项、TypeScript、改动文件 ESLint。独立只读审查最初发现删除与内部 revision/assignment 未提交写入的竞争，已修复并补并发验证；复审无高置信阻塞问题。具体最终计数见下方完成记录。

覆盖真实临时数据库、NAS 丢失/篡改、原 revision 恢复、新旧 Turn 差异、未知快照、旧 payload、字符串参数、排队/运行/暂停依赖、终态 ready 不阻塞、组织与平台边界、RLS/缺表失败关闭、旧停用数据、并发删除/任务写入/版本授权写入、审计失败原子回滚，以及删后不可管理和标识不复用。

明确“提交部署”后才经 release.sh 发布当前分支并应用 261。发布前只读确认生产 tasks/checkpoints 的权限与 RLS 状态（预检失败不能改为放行）。部署后用户验证：
1. 停用已发布测试 Skill → 更多操作直接废弃；确认提示新任务仍禁用、旧任务恢复重新允许。
2. 废弃详情没有启用/停用/编辑/发布，仅正文、历史、返回及删除检查；平台详情无删除。
3. 有未结束引用或未知状态时删除置灰，原因可见；任务结束后重新检查允许删除。
4. 确认删除后列表移除，刷新与直接管理 URL 不可再打开；旧历史/NAS/审计仍保留，不能以同 key 重建。
5. 两个管理页面同时操作，过期版本或变化的依赖被拒绝；失败保留记录，不能显示删除成功。

### 回滚

基准为 `254410e77a3e8f2e3d957f8a8a3bed71baf88313`。尚无 deleted_at 记录时，261 回滚恢复 260 guard/audit 函数并移除新增元数据，不删除 Skill 或 NAS。已有删除标记时回滚脚本主动拒绝，防止条目重新出现；此时必须保留 261 标记、过滤及保护，采用前向修复，不能直接部署旧版应用或删除列。所有发布/回退走受控 release.sh，当前未提交部署、未合并 main、未清理工作树。

完成记录：后端定向测试共 152 项通过（safe removal 29、authoring PostgreSQL 38、reenable 17、catalog PostgreSQL 27、authoring API 41），前端 34 项通过（管理 32、导航 2）。新增检查对非标准 UUID 形态的旧快照按未知处理，避免错误当作无引用。TypeScript、定向 ESLint 与 git diff --check 通过。只复验受改动影响的路径，不执行全量测试；生产迁移和真实界面操作尚未执行。

## 历史暂停任务误拦删除修复（2026-09-21，未部署）

前述 261 和界面已随 `705d3044b3810fc07fc324f0c7e01e1a8bd1f622` 部署，构建、迁移和健康检查通过；以上“未部署”为当时开发记录。本节为该版本线上复验后新增的修复，尚未提交部署。

### 根因与证据

“测试日报”删除预检返回 `blocking_tasks=0, uncertain_tasks=36`。36 个都是暂停聊天，快照最后写入时间为 08-28 至 09-18 17:24:33（北京时间），没有手动选择或 Skill runtime 记录；Skill 包创建于 09-20 22:19:46。261 对所有非终态且缺少 runtime 的任务统一标未知，没有区分能以服务器保存时间证明无关的历史暂停任务。原测试只覆盖损坏/缺失记录，未覆盖正常旧版本快照。

### 改动和约束

- 新增 `backend/migrations/262_skill_legacy_pause_removal.sql`，不改已部署的 261。服务预检、最终删除和数据库 trigger 仍复用 `skill_deletion_blockers`。
- 只对同时满足以下事实的记录豁免“缺少 runtime”：任务与 checkpoint 都为 paused，checkpoint 与任务 Turn 一致，checkpoint 更新时间严格早于包的不可变创建时间，请求参数为对象且不存在 `_selected_skill`，快照为对象、兼容 payload 包装且内外均不存在 `skill_runtime`。仅按任务创建时间或固定日期不能豁免。
- 显式引用不受豁免影响；正在运行/已恢复、时间等于或晚于包创建、Turn 不符、缺快照、显式 null/异常 payload/字符串参数继续失败关闭。最终删除的 SHARE 锁及检查不变，历史任务在预检后恢复会被重新判定。
- 创建时间由 `skill_deletion_package_created_at` 在原有 forced RLS 和可信组织作用域内读取；客户端不能提供时间。该辅助函数 SECURITY INVOKER、固定 search_path、仅服务角色可调用；其 `row_security=on` 局部配置退出后还原，原任务扫描仍 `row_security=off`，防止 RLS 隐藏依赖后误报零。没有改 Actor、旧 Runtime、前端或 NAS，也不取消/修改旧任务。
- 测试文件：新增 `backend/tests/test_skill_legacy_removal_postgres.py`；更新 `backend/tests/test_skill_removal_postgres.py` 的真实快照字段及迁移加载。记录同步本文和 `docs/CURRENT_ISSUES.md`。

### 验证

先用原 261 复现裸快照和 payload 快照两项失败；修复后两文件共 **50 passed**（新增 21、已有删除保护 29），覆盖历史兼容、真实引用、异常边界、并发恢复复查、组织权限、RLS 失败关闭、数据库 trigger 及回滚重放。未执行无关全量测试。

生产使用实际后端数据库角色，在 `BEGIN READ ONLY` 事务中对同一 Skill 执行已部署函数和候选 SELECT：旧值 `0/36`，新值 `0/0`（实际引用/未知任务）。只读对比未安装函数、执行迁移或写入业务数据，不等于已上线。

### 生产验证与回滚

收到本次“提交部署”后经 `deploy/release.sh` 应用 262。用户刷新废弃 Skill 详情，点击“重新检查”；在任务状态未变化的前提下，原 36 个无关历史任务不再拦截，删除按钮可用。实际删除仍由用户确认。存在真实引用或检查失败时应继续禁用，不能强制放行。

应用基准为 `705d3044`。本次只新增查询函数迁移；`backend/migrations/rollback/262_skill_legacy_pause_removal_rollback.sql` 恢复 261 保守检查并移除辅助函数，不删除任何删除标记、版本、NAS 或审计。已有逻辑删除也可保留回退查询，但历史任务误拦将重新出现。禁止为了回退本修复撤销 261 的删除保护。
