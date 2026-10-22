# 统一知识画布 V1 开发交付

日期：2026-10-19。任务：`codex/task/20261019033948-knowledge-canvas-design`。开发基线已快进到 `8d24368b`；主线变化仅涉及商品图片删除，本任务不修改该业务。状态：开发环境真实闭环已实现；用户已授权“提交部署”，本记录随生产候选提交。生产结果以受控流水线的最终 SHA 和运行结果为准，不能把本地验证当作生产成功。

主方案：[实施方案 §9.3](./TECH_统一知识画布实施方案.md#93-可持续开发与生产预览计划)。B0 契约仍描述全周期目标；本文件及实际 [V1 OpenAPI](./knowledge-canvas-v1/openapi.json) 明确本版子集和局部细化，不代表 B2—B5 已完成。

## 本版可用

- `/knowledge` 受保护页面；主对话侧栏在服务端确认已开放后才出现“知识画布”入口。
- 单画布，右侧顶部个人/项目/团队三分类；无右侧创建按钮，无聊天框、聊天节点、@ 模拟回答或未接入的定时按钮。
- 双击画布或拉线到空白创建主题、文档、规则；从画布创建项目；归属选择，子节点继承所属容器。
- Tab 子节点、Enter 同级、F2 标题、拖动布局、空间键平移、缩放、分支折叠、目录定位与返回视野。
- 展开读取和保存纯文本正文，正文与标题分别形成不可变修订；刷新/重新登录读取数据库，不以 localStorage 保存业务内容。
- 项目 manager 通过节点右键“项目成员权限”选择本组织有效成员，授予查看/编辑/管理或移除；不能修改自己的管理角色。
- 冲突不覆盖最新内容，编辑草稿保留。未知保存结果复用原 command_id 重试；未核对旧操作前不混入另一笔写入。

本版目录输入框明确称“标题筛选”，仅筛选当前容器已加载标题；全文检索在 V2 接入。切换目录选项卡不移动画布；点击节点目录会更新当前项目范围、展开祖先并定位。选中节点置于其他重叠节点前方。

## 数据、权限与接口

迁移：[302_knowledge_workspace_v1.sql](../../backend/migrations/302_knowledge_workspace_v1.sql)。新增 9 张底座表：容器、项目、成员、对象、对象版本、画布位置、关联、命令回执、事件。没有修改 Agent 经验知识表或商品图片项目表，没有安装新应用依赖。

运行时数据库角色只有新表 SELECT 和受控函数 EXECUTE；直接 INSERT/UPDATE/DELETE 不授权。写入集中到 kb_apply_command，由真实 DatabaseScope 构造事务身份，检查容器 ACL、锁定容器、校验版本、写内容/布局/事件/回执。新表强制 RLS；权限 helper 固定 search_path，PUBLIC 无函数执行权，通过 owner 专属策略读取成员，避免 RLS 自递归。private 不因组织管理员身份开放；org=NULL 仍按本人隔离。运行时角色实测不具 superuser/BYPASSRLS。

V1 受控命令：create_project、create_object、save_document、update_title、reparent、move_nodes、connect、set_member。reparent 的防环与跨容器拒绝已实现并验证；拖线到已有节点创建 related 关联，不隐式移动父子层级。

在 B0 上新增局部接口：GET /availability（只读可用性）、POST /initialize（幂等创建个人/团队默认空间）、GET /containers/{id}/members（限 manager）。其余本版接口为 GET /canvas、GET /objects/{id}、POST /commands。均复用既有认证、组织上下文、客户端和 success/data 包装，知识错误采用 error/code/message/details；禁止用客户端身份字段覆盖权限。

本版一对象只有一个画布位置，关联仅限同容器；多位置展示及跨容器引用仍是后续能力。create_object 仅开放 topic/document/rule，file/work_item 等到对应闭环后再启用。画布节点以 500 项分页，客户端最多加载 10,000 项；容器最多 1,000 个、关联最多 2,000 条，超出明确报错。这是容量边界，不是大规模性能已达标的承诺。

## 独立开发验证与复现

本任务使用自己的 loopback PostgreSQL/Redis 原生实例。普通 bootstrap 库与收据保持不变；[knowledge_v1.py](../../scripts/cloud/knowledge_v1.py) 从已核验的空库基线克隆 `cloud_dev_knowledge_v1`，校验任务目录、集群身份、迁移摘要和结构指纹后才运行本版 SQL。只记录开发收据，不写生产迁移账本。合成用户、组织、JWT 和本地连接配置仅留在忽略目录中，不进入补丁。

已完成验证：

- 后端 99 项通过：16 项 V1 请求/真实数据库检查与 83 项既有租户隔离回归；覆盖个人/组织隔离、角色、撤权、幂等、原子回滚、防环、并发修改、并发重复创建及实际 runtime 角色 RLS。
- 前端 12 项通过：8 项画布行为与 4 项侧栏导航回归；包含只读、范围切换、同级归属、冲突草稿和未知结果重试。
- [8 项真实浏览器场景](./knowledge-canvas-v1/browser-results.json)通过：页面调用实际 FastAPI 与私有 PostgreSQL，未 mock 路由；含正文/布局刷新恢复与第二账号只读页面，无页面异常、无外部 HTTP 源请求。
- 新增前端实现文件的 ESLint 与 TypeScript/Vite 构建通过；仓库现有大 chunk 提示仍在，不将其当作本版性能验收。
- 已人工检查[实际页面截图](./knowledge-canvas-v1/preview.png)，修正主题背景、模态框和重叠节点定位。

任务根目录的开发启动（首次安装按 CLOUD.md，本任务已有依赖）：

```bash
.venv/bin/python scripts/cloud/knowledge_v1.py prepare
.venv/bin/python scripts/cloud/knowledge_v1.py start
```

另一个终端：

```bash
npm --prefix frontend run dev -- --host 0.0.0.0 --port 3000
```

前端开发入口为端口 3000 的 `/knowledge`，API 仅在 loopback 8000。需要本开发库的已认证账号；现有生产账号不在此私有库中。本版未增加免认证体验入口。

真实浏览器验证先执行 `knowledge_v1.py browser-session`，只生成本任务合成会话到私有文件；再执行 `KB_PLAYWRIGHT_MODULE=<环境已有 Playwright 模块路径> node scripts/cloud/knowledge_v1_browser.cjs`。后端在 backend 目录用 `KNOWLEDGE_V1_INTEGRATION=1 ../.venv/bin/python -m pytest tests/test_knowledge_workspace.py tests/test_org_scoped_db.py tests/test_org_scoped_db_upsert.py tests/test_org_scoped_db_isolation.py -q`；无显式集成标记时跳过私有数据库用例。

## 生产候选条件与后续

用户在本次发布中明确要求“直接开放好了没关系”，因此 V1 改为向所有已登录账号开放：`KNOWLEDGE_WORKSPACE_ENABLED` 默认 true，两个名单默认空。显式设置 false 可关闭；若未来设置非空 `KNOWLEDGE_WORKSPACE_USER_IDS` 或 `KNOWLEDGE_WORKSPACE_ORG_IDS`，则仅匹配的账号/组织开放。所有接口同样检查，前端隐藏不是授权。开放页面不扩大内容 ACL，个人、项目和团队权限仍独立检查；不包含项目级功能发布名单。

生产发布已获用户“提交部署”授权，但尚未成功，不能视为已上线或已验收。2026-10-19 的候选 `688d95e3f50aa5c784344bc0c225a6496c3c31ce` 已通过[最终 SHA 完整检查](https://github.com/wucong75644-crypto/EVERYDAYAIONE/actions/runs/39271703028)，随后[受控发布失败](https://github.com/wucong75644-crypto/EVERYDAYAIONE/actions/runs/39272234381)：`executor_state=unconfirmed`，发布锁保留。不能据此判断迁移未执行、文件未同步或旧版本未受影响。

15:04 UTC 的[独立只读探测](https://github.com/wucong75644-crypto/EVERYDAYAIONE/actions/runs/39274734639)确认五个服务均 active、固定路径存在、网关/脚本策略身份匹配、任意 SSH 命令仍被拒绝；同时锁存在、生产成功标记为空。该探测没有查询业务、连接数据库/Redis、读取应用配置、同步文件或重启服务；服务 active 不能替代应用就绪和功能验收。探测作业按预期保持失败状态，未将锁异常改成成功。

诊断发现 `verify_delivery.py` 在探测非零退出时丢弃了已返回的服务状态；已修复为在身份校验后只发布固定协议的安全字段，仍拒绝异常生产状态。新增探测测试与既有网关、交付测试共 36 项通过。原发布器仅保留允许公开的结果枚举，底层错误没有进入作业日志；当前固定网关也不提供服务器日志和进程核查接口，因此具体失败命令尚未确认。

恢复前需通过原有管理连接核查：残留发布/迁移进程、服务器日志、文件与迁移实际状态、服务就绪及锁所有权。先保留证据，确认远端写入已停止后再按协调协议恢复；不直接删锁、伪造成功标记或重跑不确定写入。不在开发 VM 直接连接生产或读取生产凭据。修复后重新整合最新 main，检查最终候选，再从受控发布入口继续，不能复用旧 SHA 的成功检查来部署新提交。

开关和名单来自进程 Settings，修改部署配置后需通过受控流程重启生效，不是已有管理后台的实时开关。关闭能力时先关服务端开关；新增页面/API 不再可用，原主对话、文件与调度保留原行为。V1 尚无后台知识作业，应用可回退至兼容旧版本，同时保留新增表和内容，不执行 DROP 回滚。后续 V3/V4 含运行中任务时必须采用主方案更严格的排空/暂停步骤。

未完成能力按既定版本推进：V2 文件/全文检索/版本查看，V3 主对话区 @，V4 分支定时与企微，V5 多人实时协作与扩量。V1 无删除/恢复与撤销界面、多位置节点、大画布虚拟化、移动端完整交互或富文本共同编辑；这些不作为本版已验收能力。既有 F01/F02 未修改，仍是 V4 上线前置。

## 2026-10-22 本地恢复诊断

- 已确认失败流水线结束、原锁所有者仍为 `20261019144845-2277-23513-5483`，生产无在途迁移；五个服务 active，`/api/health` 返回 200。生产文件已同步 V1，但 9 张知识表和 302 迁移账本均不存在，不能视为成功候选。
- 在匹配生产身份的数据库中以原执行角色运行迁移，并强制事务回滚，复现首表创建报错：`permission denied for table organizations`。现有身份表归 everydayai 所有，迁移角色 everydayai_owner 缺少 REFERENCES 权限。
- 302 迁移先切换现有表所有者 everydayai，仅授予 organizations.id/users.id 的 REFERENCES 给 everydayai_owner，再回到知识表所有者。没有添加读取或写入身份数据的授权。
- 修复后的完整 SQL 在生产同角色事务中执行成功后 ROLLBACK，未留下知识表、权限或迁移账本变更；正式迁移仍仅由受控发布执行。网关 10 项测试通过。
- 发布恢复进行中，尚未恢复成功标记或宣称部署完成。

## 2026-10-22 发布流程简化与恢复执行

用户明确选择本地受控发布。当前入口为 deploy/release.sh，GitHub 历史生产开关已设 false，新生产/验收工作流停止执行；CI 检查仍可单独使用。新增精确 owner/失败 SHA 的显式恢复入口，核对文件摘要、无在途发布/迁移、独立数据库身份和健康后，归档文件/账本/旧锁并写原任务恢复记录；不伪造成功候选。下一次完整发布成功才重建成功标记。私有日志持久保存在 Git 公共目录 release-logs/，附安全阶段标识；测试/构建失败且尚未写入时保留旧成功候选。

61 项发布/恢复/兼容命令测试通过；后续路径保护与本地命令路由 13 项验证通过。旧失败 688d95e3 的恢复已实际成功，证据为 `/var/www/everydayai/.incident-evidence/release-recovery-20261022T123546Z-20261019144845-2277-23513-5483`。原锁已归档，未删除现场；生产权限/结构未由恢复命令改写，正式迁移待正常发布执行。
