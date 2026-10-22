# EVERYDAYAIONE 协作约束

先读取 [共同协作规则](docs/development/COLLABORATION.md)。技能位于 `.agents/skills/`。

## 任务边界

- 每个 Codex 对话对应独立任务工作树，分支为 `codex/task/*`。新任务用 `./scripts/task-worktree.sh start <slug>` 从最新 `origin/main` 创建；接续云端任务沿用原分支。
- 主工作树已有修改属于用户，禁止覆盖、暂存、提交、清理或混入其他任务。
- 本项目不再使用 AOCI；不接入 MCP、不维护索引、不恢复历史 Runtime 平台路径。

## 发布与验收

- 用户说“提交部署”即授权提交当前任务、合入最新 main、验证并部署到生产供测试；不合并 main、不清理工作树。
- 生产发布、验收和故障恢复统一走**本地** `deploy/release.sh`。GitHub 仅保存代码、运行可选自动检查，不持有或执行生产发布职责；不创建 production/accept/verification 请求标签。
- 有新修改：根据实际 diff 生成提交信息和精确文件清单，执行 `bash deploy/release.sh --message ... --file ...`。没有新修改：`bash deploy/release.sh --deploy-task <HEAD>`。
- 默认完整发布前后端。入口同步最新 main、锁定候选，执行测试/构建、生产数据库身份核验、迁移账本检查、服务重启和 readiness。冲突保留现场，在同一授权内理解双方修改并解决、验证，不用 ours/theirs 丢弃任一任务。
- 测试、构建或线上检查失败即停止；不以旧检查放行新 SHA，不绕过锁、迁移或健康检查。其他未验收任务不得被覆盖。
- 失败恢复：核对流水线/进程已结束、原锁所有者、实际文件/迁移/健康状态。显式执行 `bash deploy/release.sh --recover-lock <原owner> --failed-commit <完整SHA>`。入口保留私有证据、归档原锁，只生成绑定原任务的恢复记录；它不代表发布成功、不执行迁移。再正常提交部署。
- 用户说“清理工作树”才运行 `bash deploy/release.sh --accept-and-close`：核验生产测试候选，合并 main，确认树一致，同步其他任务稳定基座元数据后清理。此阶段不重复部署，也不修改其他任务正在开发的代码。
- 禁止直接运行 `deploy/deploy.sh`。发现废弃 Runtime 平台路径必须停止。详细命令和失败恢复见 [发布流程](docs/development/APPROVED_DELIVERY.md)。

## 云端开发

- 云端只开发和测试，不读取生产凭据、不直连生产；完成后推送任务，由本地接续受控发布。不要上传 deploy/config.env、应用 .env、SSH 私钥或生产数据。
- 新任务运行 `scripts/task-worktree.sh start <slug> --development-only`；接续任务复用原工作树。配置和启动见 [云端开发指南](docs/development/CLOUD.md)。
- 不自动执行生产迁移、导入或种子数据。完整原生模式仅按 [开发 bootstrap](docs/development/CLOUD_SCHEMA_BOOTSTRAP.md) 初始化本任务经核验的全新 loopback 开发库。
