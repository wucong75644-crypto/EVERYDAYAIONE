# Codex 云端开发

本仓库支持云端开发、测试，本地生产发布。共同规则在 `COLLABORATION.md`，项目约束在根目录 `AGENTS.md`；适用技能已经放在 `.agents/skills/`，无需读取 `/Users/...` 或安装个人目录技能。

## 创建环境

在 Codex 的 Work in → Cloud → Create environment 中连接 GitHub，选择 EVERYDAYAIONE。必须选择包含本次适配的代码版本；尚未推送的本地文件不会被云端读取。环境安装阶段只配置工具，不开发业务代码。

向环境配置任务说明：

> 请读取 AGENTS.md 和 docs/development/CLOUD.md。准备 Python 3.12、Node.js 22.12+、npm 和 Git，执行 bash scripts/cloud/setup.sh。只准备开发依赖；不配置生产 SSH、不执行 deploy 下的脚本、不迁移数据库。将 .agents/skills/cloud-development/SKILL.md 的启动流程用作 Start skill，完成检查后发布环境。

安装入口不安装系统运行时；Python 默认读取 python3，可通过 CLOUD_PYTHON=python3.12 指定。Node 支持 22.12+ 或 20.19+；推荐 22。依赖沿用 frontend/package-lock.json 和 backend/requirements.txt，不另建易失真的依赖清单。

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

## 本地接续与部署

云端开发结果在用户授权后提交、推送到对应 codex/task/* 分支，记录 SHA 及验证结果。未推送的工作不能靠本地 fetch 取回。

本地先 fetch 该任务分支并核对是否已有同名工作树；有则继续原工作树，无则将已推送分支检出到新的独立工作树（不是重新 start 一个不同分支）。例如确认本地不存在该任务分支或工作树后，执行：

```bash
git fetch origin
git worktree add --track -b codex/task/<任务分支后缀> ../EVERYDAYAIONE-worktrees/<任务目录> origin/codex/task/<任务分支后缀>
# cd 到新工作树，核对 git rev-parse HEAD 与云端 SHA 一致
```

部署配置由本地维护者在本地补充为 deploy/config.env，权限 0600，不通过 Git 传输。确认本地 HEAD 就是云端交付 SHA，检查本地已有修改后再继续。

只有用户明确“提交部署”后，才在本地运行 `deploy/release.sh --message ... --file ...`；“清理工作树”仍经本地 `--accept-and-close`。云端不执行发布、生产验收或关闭流程；不会因为云端测试完成自动合并 main。
