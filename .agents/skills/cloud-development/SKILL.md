---
name: cloud-development
description: 配置或启动 EVERYDAYAIONE 的 Codex 云端开发环境，以及将云端开发结果交给本地部署。适用于云端安装、开发服务检查和启动，不负责生产发布。
---

先读根目录 AGENTS.md 和 [云端开发指南](../../../docs/development/CLOUD.md)。

环境安装阶段在检出的仓库运行 `CLOUD_PYTHON=python3.12 bash scripts/cloud/setup.sh` 安装依赖。已明确选择完整原生开发环境时使用 `--with-services`，从经校验的资源准备仅 loopback 的 PostgreSQL/pgvector 和独立 Redis，并初始化本任务全新空白开发库；不连接生产、不执行生产迁移或恢复旧平台。
适配尚未合入 main 时，先核验环境检出的适配分支与交付 SHA，并在 start 命令追加 `--base <已核验适配提交SHA>`；合入 main 后恢复默认基座。
实际业务任务首次修改前运行 `scripts/task-worktree.sh start <slug> --development-only` 并切换到输出路径；继续任务复用原任务工作树。
新工作树不会继承虚拟环境或 node_modules，在该工作树运行安装入口。先读取已有配置，只在不存在时生成 backend/.env，不复制根目录或其他任务的 .env。

完整模式每次先运行 `.venv/bin/python scripts/cloud/services.py prepare "$PWD"`，使服务在 VM 重启后重新启动并验证 bootstrap receipt、完整结构/函数/权限 fingerprint。然后 `.venv/bin/python scripts/cloud/dev.py check`、`.venv/bin/python scripts/cloud/dev.py start`；只有明确只做前端时使用 `start --frontend-only`。启动后运行 `.venv/bin/python scripts/cloud/smoke.py`，覆盖合成组织/用户 API、越权拒绝和至少 20 秒后台观测，不能只凭健康接口 200 判断就绪。命令保持在任务终端会话内；结束时向该会话发送 Ctrl-C 或向本次 `.cloud-dev/readiness.json` 中已核实的 supervisor PID 发送 SIGTERM。不按进程名批量杀服务。

开发 bootstrap 仅允许当前工作树散列对应的私有实例；数据目录与密码按任务隔离，Redis 使用独立进程及 DB 15，不能用逻辑 DB 号替代进程隔离。未知非空库、损坏 receipt/checksum 或结构漂移会明确拒绝，禁止覆盖数据或伪造“历史迁移已应用”。现有用户配置不属于此实例时保留并停止。详见 `docs/development/CLOUD_SCHEMA_BOOTSTRAP.md`。外部模型/短信/OSS、沙盒与外部自动化没有单独授权与开发凭据时均不启用。
日志在忽略目录 `.cloud-dev/`，可能包含连接信息，禁止完整贴到对话或提交。

日常测试和页面预览留在本任务云端环境。准备提交部署时，先在明确授权下按受控入口提交推送，再运行 `python3 scripts/ci/request-check.py` 请求 GitHub **Pre-release checks**；等待同一完整 SHA 的 frontend/backend 两个 job 成功并报告耗时。普通业务修改不必每次触发全量检查。新提交、main 合入或冲突解决后重新检查，不复用旧 SHA。此检查流水线不执行生产部署，通过也不代表已获生产发布授权。详见 `docs/development/PRE_RELEASE_CHECKS.md`。

生产部署和验收关闭仅在本地进行。交付任务分支、已授权推送后的提交 SHA、变更与验证摘要、必要开发配置名称（不含值），按指南在本地接续同一任务。
