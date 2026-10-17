# EVERYDAYAIONE 协作约束

先读取仓库内的 [共同协作规则](docs/development/COLLABORATION.md)。本地和云端均以此文件为共同规则来源；技能位于 `.agents/skills/`。

## 任务边界

- 每个 Codex 对话对应一个独立任务工作树，任务分支必须是 `codex/task/*`。
- 新任务使用 `./scripts/task-worktree.sh start <slug>` 从最新 `origin/main` 创建。
- 当前主工作树已有的修改属于用户，禁止覆盖、暂存、提交、清理或混入其他任务。

## 发布与关闭

- 用户每次说“提交部署”都表示：提交当前任务分支、推送并部署到生产供测试；不合并 `main`，不清理工作树。
- 若当前任务没有新改动，使用 `deploy/release.sh --deploy-task <HEAD>` 重新部署该任务已推送的提交。
- 用户明确说“清理工作树”表示验收通过：先核验当前任务提交就是生产已测试版本；再合并到 `main`、确认最终代码树与该候选一致、同步其他活跃任务的稳定基座，最后清理当前工作树和本地分支。
- “清理工作树”阶段不重复部署。若最终 `main` 与已测试候选不一致，必须停止并保留任务；用户需重新“提交部署”最终版本后再验收。
- 稳定基座同步只更新其他 `codex/task/*` 工作树的本地 Git 元数据，不改动它们正在开发的代码。

## 受控入口

- 提交部署使用 `deploy/release.sh --message ... --file ...`。
- 验收关闭使用 `deploy/release.sh --accept-and-close`；禁止直接运行 `deploy/deploy.sh`。
- 发现旧 Runtime 平台路径出现在待发布或验收提交中，必须停止并报告。

## 已撤销的集成

- 本项目及所有任务工作树不再使用 AOCI，不接入 MCP，不执行索引读取、维护、启动或发布核验。历史文档中的接入要求已撤销。

## 云端开发

- 云端只开发和测试，生产发布、验收合并和关闭在本地执行；不要上传 `deploy/config.env`、生产 `.env`、SSH 私钥或生产数据。
- 每个云端任务首次修改前运行 `./scripts/task-worktree.sh start <slug> --development-only`，切换到输出路径。继续任务复用原工作树；不能使用其他任务的分支。
- 环境安装入口：`bash scripts/cloud/setup.sh`；任务工作树内配置及启动流程见 [云端开发指南](docs/development/CLOUD.md)。
- 不自动执行数据库迁移、导入或种子数据；只连接独立开发数据库、Redis 和开发服务。
- 云端交付记录任务分支、提交 SHA、验证结果和未完成项；推送须有用户授权。本地接续同一任务分支，沿用受控发布入口。
