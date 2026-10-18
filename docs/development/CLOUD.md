# Codex 云端开发

本仓库支持云端开发、测试；生产发布可使用GitHub的Codex指令发布流水线或既有本地受控入口。配置和隔离演练见 [审批发布](APPROVED_DELIVERY.md)，配置演练时禁用生产执行。共同规则在 `COLLABORATION.md`，项目约束在根目录 `AGENTS.md`；适用技能已经放在 `.agents/skills/`，无需读取 `/Users/...` 或安装个人目录技能。

日常测试和页面预览在 Codex 云端执行；发布候选提交后，再请求 GitHub 的独立检查。入口、范围、超时和结果核验见 [发布前检查](PRE_RELEASE_CHECKS.md)。这条检查流水线不连接生产、不执行部署，也不改变现有生产验收顺序。

## 创建环境

在 Codex 的 Work in → Cloud → Create environment 中连接 GitHub，选择 EVERYDAYAIONE。必须选择包含本次适配的代码版本；尚未推送的本地文件不会被云端读取。环境安装阶段只配置工具，不开发业务代码。

向环境配置任务说明：

> 请读取 AGENTS.md 和 docs/development/CLOUD.md。准备 Python 3.12、Node.js 22.12+、npm 和 Git，执行 bash scripts/cloud/setup.sh。只准备开发依赖；不配置生产 SSH、不执行 deploy 下的脚本、不迁移数据库。将 .agents/skills/cloud-development/SKILL.md 的启动流程用作 Start skill，完成检查后发布环境。

安装入口不安装系统运行时；Python 默认读取 python3，可通过 CLOUD_PYTHON=python3.12 指定。Node 支持 22.12+ 或 20.19+；推荐 22。依赖沿用 frontend/package-lock.json 和 backend/requirements.txt，不另建易失真的依赖清单。

完整原生开发模式使用 `CLOUD_PYTHON=python3.12 bash scripts/cloud/setup.sh --with-services`。Python/Node 仍须预装；该显式选项额外校验并在工作区解包精确版本的 PostgreSQL 17、pgvector、Redis，创建任务私有 loopback 数据服务及经审计的空库 bootstrap。默认不带选项仍仅安装依赖。此路径不读取生产结构或数据，也不运行 production migration_runner。结构来源、失败/重试边界与验证见 [原生空库初始化](CLOUD_SCHEMA_BOOTSTRAP.md)。

允许访问 npm/PyPI 包源及依赖下载域名；功能测试所需 API 逐项添加域名。环境变量在 Codex Environment variables 或个人 vault 中配置，HTTPS API 凭据可按云端支持情况使用 Network secrets。不要上传 deploy/config.env、生产 .env、SSH 密钥、用户数据或个人技能账号缓存。

## 每个开发任务

```bash
./scripts/task-worktree.sh start feature-slug --development-only
# cd 到 TASK_STARTED 输出的 path，然后执行：
bash scripts/cloud/setup.sh
.venv/bin/python scripts/cloud/dev.py start --frontend-only
```

每次任务使用独立 codex/task/* 分支及工作树。适配尚未合入 main 时，首次环境配置应检出已推送的适配分支并核验 SHA；业务任务创建命令需追加 `--base <已核验适配提交SHA>`，否则默认 origin/main 尚不包含这些入口。适配经本地验收合入 main 后，恢复从最新 origin/main 开始。已有任务继续使用原路径。安装和状态位于任务目录中，首次创建工作树后需安装其依赖，不依赖其他任务的缓存。`--development-only` 即使本机有部署配置也不会复制它；原有本地 start 行为保持不变。

前端使用 3000 端口，对外监听 0.0.0.0。浏览器通过云端预览访问该端口；保持 `/api` 相对路径，Vite 自动代理本机 8000 API 和 WebSocket。前端模式只能验证界面及 mock 测试，需要后端的登录、数据和生成请求不可用。不要把 VITE_API_BASE_URL 指向生产。

## 后端与数据服务

`setup.sh` 首次生成权限为 0600 的 `backend/.env`，模板为 `scripts/cloud/development.env.example`，JWT 使用随机值，文件工作区指向本任务 `.cloud-dev/workspace`。重复执行不会覆盖现有配置。进程环境变量优先于该文件，可使用云端 vault 提供开发配置。

完整后端需要 PostgreSQL（项目生产采用 16 + pgvector）和 Redis，必须使用独立开发实例、账号及开发数据，不连接生产。默认模板使用 loopback 地址和 Redis DB 15；更改为你的开发资源，并配置开发专用密码。Redis 逻辑 DB 不能代替实例或账号隔离。后端启动包含任务恢复及后台消费，不能用生产 Redis 或生产数据库试启动。

数据库须先具备与任务代码匹配的结构和脱敏开发数据。仓库 deploy/init-database.sql 是历史合并基线，不能据此假定包含后续所有迁移；本适配不自动执行它或批量执行迁移。由维护者提供经批准、脱敏且结构匹配的开发快照，或在单独授权的开发数据库初始化任务中核验迁移链。没有数据资源仍可完成前端与 mock 单元测试。

已选择完整原生环境时无需生产 dump 或快照：在任务目录执行 `setup.sh --with-services`，使用独立身份的 `scripts/cloud/bootstrap/schema.sql` 和纯仓库注册表元数据 `seed.sql` 初始化空库。每个任务目录获得独立 cluster、Redis 进程、密码和动态端口，状态在 `/workspace/.everydayaione-dev/instances/<任务路径散列>/`，配置写入本任务私有 `.env`。此开发 baseline 不声明任何历史迁移已应用；只在空库内原子提交自己的 receipt。重复执行仅核验，未知非空库或任何结构/权限漂移均拒绝。迁移账本、历史迁移 checksum 和生产门禁保持不变。

发布不保留进程。完整启动顺序为：

```bash
.venv/bin/python scripts/cloud/services.py prepare "$PWD"
.venv/bin/python scripts/cloud/dev.py check
.venv/bin/python scripts/cloud/dev.py start
# 在另一个终端进行真实 API 和后台观测：
.venv/bin/python scripts/cloud/smoke.py
```

`smoke.py` 只连接本任务验证过的 loopback 库，生成合成用户、组织和对话，不调用任何外部 provider；因此其测试账号/数据仅保留在私有开发目录，不能加入补丁或 seed.sql。预检使用完整 catalog fingerprint（列、约束、函数体/签名、RLS、角色权限）而非三张表存在性。日志位于本任务 `.cloud-dev/`，应用日志也转入其私有子目录。

检查及完整启动：

```bash
.venv/bin/python scripts/cloud/dev.py check
.venv/bin/python scripts/cloud/dev.py start
```

检查校验开发模式、外部自动化关闭、任务内文件路径，连接数据库验证核心表及 Redis PING。它只读检查，不保证每个业务表或外部能力均可用。启动采用前台监督进程，日志在 `.cloud-dev/`，等 API `/api/health` 和前端 HTTP 返回就绪；Ctrl-C 或终端 SIGTERM 清理本命令创建的进程。端口被占用直接报错。日志可能含敏感连接错误，勿提交或全文转发。

按任务需要配置开发专用 APP_OPENROUTER_API_KEY、DASHSCOPE_API_KEY、OSS 等值；未配置的模型生成、短信、OSS、企业微信等能力不视为验证通过。沙盒默认关闭，云端 VM 不自动等同产品 nsjail 沙盒；测试沙盒需另行验证工具及隔离条件。不要启动 sync_worker_main.py、conversation_worker_main.py 或 wecom_ws_runner.py，除非任务已明确授权使用独立测试资源。

## 验证

```bash
# 前端：选择与变更相关的测试文件，也可执行完整单测
cd frontend
npm run test:run -- <test-file>
npm run build
# 后端：从 backend 目录，设置开发配置后运行相关 mock 测试
# ../.venv/bin/python -m pytest tests/<test-file>.py
```

测试中的 APP_ENV=testing 不能替代数据库隔离；不要带生产凭据运行测试。启动检查、mock 测试、数据库集成验证和真实外部 API 验证应如实区分。

## Codex指令发布与可选本地接续

已配置 [Codex指令发布与演练](APPROVED_DELIVERY.md)：用户在本任务说“提交部署”即授权，Codex调用task_delivery.py整合最新main、检查最终SHA、请求自动排队发布；无需GitHub点批准。说“清理工作树”授权独立accept，成功后复核并关闭原任务，不重复部署。配置/演练不触发真实发布，Codex不持有生产凭据。

以下为保留的本地接续路径：

云端开发结果在用户授权后提交、推送到对应 codex/task/* 分支，记录 SHA 及验证结果。未推送的工作不能靠本地 fetch 取回。

本地先 fetch 该任务分支并核对是否已有同名工作树；有则继续原工作树，无则将已推送分支检出到新的独立工作树（不是重新 start 一个不同分支）。例如确认本地不存在该任务分支或工作树后，执行：

```bash
git fetch origin
git worktree add --track -b codex/task/<任务分支后缀> ../EVERYDAYAIONE-worktrees/<任务目录> origin/codex/task/<任务分支后缀>
# cd 到新工作树，核对 git rev-parse HEAD 与云端 SHA 一致
```

部署配置由本地维护者在本地补充为 deploy/config.env，权限 0600，不通过 Git 传输。确认本地 HEAD 就是云端交付 SHA，检查本地已有修改后再继续。

只有用户另外明确指定本地发布时，才在本地运行 `deploy/release.sh --message ... --file ...`；“清理工作树”经本地 `--accept-and-close`，或走上述Codex指令accept入口。Codex开发环境自身不直接连接生产；不会因为云端测试完成自动合并main。
