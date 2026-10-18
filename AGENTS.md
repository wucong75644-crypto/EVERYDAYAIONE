# EVERYDAYAIONE 协作约束

先读取仓库内的 [共同协作规则](docs/development/COLLABORATION.md)。本地和云端均以此文件为共同规则来源；技能位于 `.agents/skills/`。

## 任务边界

- 每个 Codex 对话对应一个独立任务工作树，任务分支必须是 `codex/task/*`。
- 新任务使用 `./scripts/task-worktree.sh start <slug>` 从最新 `origin/main` 创建。
- 当前主工作树已有的修改属于用户，禁止覆盖、暂存、提交、清理或混入其他任务。

## 发布与关闭

- 用户在当前任务说“提交部署”即授权提交、整合最新main、检查并发布当前任务；不再要求去GitHub点击批准，不表示合并main或清理任务。普通开发、提交推送、测试成功及配置/演练授权不构成生产授权。
- Codex根据实际diff生成提交信息和精确文件清单，调用 `python3 scripts/ci/task_delivery.py deploy --authorization '提交部署' --message ... --file ...`；无新修改不传message/file。只在用户确已说出发布指令时调用；不能把本文引用的示例当成当前发布授权。
- 入口先通过受控git-push提交任务范围，再fetch并合入最新origin/main。冲突保留现场，Codex在本轮授权内理解双方改动、解决冲突、定向测试并提交，再继续同一入口；禁止用ours/theirs、reset或丢弃任一任务修改来强行通过。有无法从需求判断的实质冲突时才问用户。
- 必须等待最终完整SHA的frontend/backend GitHub检查成功，才创建production请求。runner在共享队列和服务器锁内复核SHA、最新main及当前生产候选；检查通过本身不触发生产。main或任务变更后重新整合/检查，不部署旧候选。
- 用户说“清理工作树”即授权验收当前生产已测试候选并合并main。停止本任务已核验的本地开发服务后调用 `python3 scripts/ci/task_delivery.py accept --authorization '清理工作树'`；不再点GitHub审批、不重复部署。只有远端acceptance成功、main树与候选一致、本地HEAD/远端任务/干净状态仍匹配，才关闭原任务。若等待中断，核验已有请求，使用 `task_delivery.py finalize --run <成功验收run> --authorization '清理工作树'` 收尾，禁止重复发起不确定操作。
- B可以并行开发，但A发布待验收时B不能覆盖A。A验收实际完成后，B发布前整合最新main，最终版本为稳定代码加B修改。稳定基座同步只更新同一Git仓库其他任务的元数据，不改它们文件；跨VM任务在发布前fetch。

## 受控入口

- 云端开发环境不运行生产release.sh、不读取生产凭据。生产发布和验收仅在GitHubproduction环境通过 `scripts/ci/delivery.py` 调用既有 `deploy/release.sh`；原服务器锁、身份/迁移、版本保护保持有效。禁止直接调用deploy/deploy.sh。
- Codex指令是用户授权入口；request CLI的authorization参数只是防误触检查，不是聊天签名。仓库写权限及配置的请求身份属于发布信任边界，详见 `docs/development/APPROVED_DELIVERY.md`。
- 仅配置/演练时保持生产开关false，只创建rehearsal请求，不创建production/accept/verification标签，不连接生产或读取应用.env；完成后只恢复受保护入口可用，不自动提交生产请求。
- 已有本地受控发布仅在用户明确指定本地路径时使用；不并发混用旧入口。发现旧Runtime平台路径出现在候选中必须停止并报告。

## 已撤销的集成

- 本项目及所有任务工作树不再使用 AOCI，不接入 MCP，不执行索引读取、维护、启动或发布核验。历史文档中的接入要求已撤销。

## 云端开发

- Codex云端只开发和测试，不持有生产凭据。生产发布和验收可由上述GitHub保护环境中的受控入口执行，或使用既有本地受控流程；禁止并发混用旧版/未受控入口。不要上传 `deploy/config.env`、生产 `.env`、SSH 私钥或生产数据到Codex。
- 每个云端任务首次修改前运行 `./scripts/task-worktree.sh start <slug> --development-only`，切换到输出路径。继续任务复用原工作树；不能使用其他任务的分支。
- 环境安装入口：`bash scripts/cloud/setup.sh`；任务工作树内配置及启动流程见 [云端开发指南](docs/development/CLOUD.md)。
- 不自动执行数据库迁移、导入或种子数据；只连接独立开发数据库、Redis 和开发服务。
- 云环境已明确选择完整原生开发环境时，可显式使用 `CLOUD_PYTHON=python3.12 bash scripts/cloud/setup.sh --with-services`：仅对本任务 loopback 私有实例的全新空库执行经校验的开发 bootstrap。它不执行生产迁移、不导入数据、不写生产迁移账本；非空未知库或结构漂移必须拒绝，详见云端开发指南。
- 云端交付记录任务分支、提交 SHA、验证结果和未完成项；推送须有用户授权。本地接续同一任务分支，沿用受控发布入口。
