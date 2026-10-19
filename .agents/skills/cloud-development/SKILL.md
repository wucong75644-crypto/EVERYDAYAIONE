---
name: cloud-development
description: 配置或启动 EVERYDAYAIONE 的 Codex 云端开发环境，以及接续受控GitHub检查和审批发布请求。开发环境自身不持有生产凭据、不直接连接生产。
---

先读根目录 AGENTS.md 和 [云端开发指南](../../../docs/development/CLOUD.md)。

环境安装阶段在检出的仓库运行 `CLOUD_PYTHON=python3.12 bash scripts/cloud/setup.sh` 安装依赖。已明确选择完整原生开发环境时使用 `--with-services`，从经校验的资源准备仅 loopback 的 PostgreSQL/pgvector 和独立 Redis，并初始化本任务全新空白开发库；不连接生产、不执行生产迁移或恢复旧平台。
适配尚未合入 main 时，先核验环境检出的适配分支与交付 SHA，并在 start 命令追加 `--base <已核验适配提交SHA>`；合入 main 后恢复默认基座。
实际业务任务首次修改前运行 `scripts/task-worktree.sh start <slug> --development-only` 并切换到输出路径；继续任务复用原任务工作树。
新工作树不会继承虚拟环境或 node_modules，在该工作树运行安装入口。先读取已有配置，只在不存在时生成 backend/.env，不复制根目录或其他任务的 .env。

完整模式每次先运行 `.venv/bin/python scripts/cloud/services.py prepare "$PWD"`，使服务在 VM 重启后重新启动并验证 bootstrap receipt、完整结构/函数/权限 fingerprint。然后 `.venv/bin/python scripts/cloud/dev.py check`、`.venv/bin/python scripts/cloud/dev.py start`；只有明确只做前端时使用 `start --frontend-only`。启动后运行 `.venv/bin/python scripts/cloud/smoke.py`，覆盖合成组织/用户 API、越权拒绝和至少 20 秒后台观测，不能只凭健康接口 200 判断就绪。命令保持在任务终端会话内；结束时向该会话发送 Ctrl-C 或向本次 `.cloud-dev/readiness.json` 中已核实的 supervisor PID 发送 SIGTERM。不按进程名批量杀服务。

开发 bootstrap 仅允许当前工作树散列对应的私有实例；数据目录与密码按任务隔离，Redis 使用独立进程及 DB 15，不能用逻辑 DB 号替代进程隔离。未知非空库、损坏 receipt/checksum 或结构漂移会明确拒绝，禁止覆盖数据或伪造“历史迁移已应用”。现有用户配置不属于此实例时保留并停止。详见 `docs/development/CLOUD_SCHEMA_BOOTSTRAP.md`。外部模型/短信/OSS、沙盒与外部自动化没有单独授权与开发凭据时均不启用。
日志在忽略目录 `.cloud-dev/`，可能包含连接信息，禁止完整贴到对话或提交。

日常测试和页面预览留在本任务环境；普通开发与检查通过不触发部署。用户明确在当前任务说“提交部署”后，按AGENTS使用 `python3 scripts/ci/task_delivery.py deploy --authorization '提交部署' --message ... --file ...`，无新改动不传message/file。它整合最新main、推送最终SHA、等待范围分类、汇总门禁及所有必要GitHub检查（分档规则见PRE_RELEASE_CHECKS.md，未知/核心改动完整检查）、请求排队发布并等待结果，无需GitHub点批准。冲突保留后由Codex理解双方需求解决、测试并提交再继续，不直接丢弃一边。失败不得换成旧候选或绕过锁。

用户明确“清理工作树”时，先停止本任务已核实的开发进程，然后 `python3 scripts/ci/task_delivery.py accept --authorization '清理工作树'`。仅在远端成功验收、main树及当前任务SHA匹配后自动关闭原任务，不重复部署、不改其他任务代码。等待中断先查已有请求，成功后用finalize收尾，不盲目重复请求。跨VM不推送实时工作区修改；B自己的部署入口会fetch并整合最新main。

仅配置/演练授权不允许创建production/accept/verification请求；只能使用rehearsal及临时Git/fake SSH。生产开关false时不尝试本地凭据。Codex不持有生产SSH、.env、数据库或Redis凭据，GitHub凭据仅在受限环境中供正式job使用。详见 `docs/development/APPROVED_DELIVERY.md`。

用户要求“排查生产问题”、查生产日志或线上服务状态时，执行 [production-diagnostics](../production-diagnostics/SKILL.md)，从注册表发现固定只读 GitHub 诊断 job 并获取新现场。此请求只授权诊断重跑，不创建发布/验收标签，不部署、不重启；生产凭据仍只在 GitHub 受限环境中使用。GitHub 连接器不依赖本任务的 VPN 执行器，但连接器缺失时必须明确报告。
