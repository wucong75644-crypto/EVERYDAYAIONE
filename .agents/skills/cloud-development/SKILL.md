---
name: cloud-development
description: 配置或启动 EVERYDAYAIONE 的 Codex 云端开发环境，以及将云端开发结果交给本地部署。适用于云端安装、开发服务检查和启动，不负责生产发布。
---

先读根目录 AGENTS.md 和 [云端开发指南](../../../docs/development/CLOUD.md)。

环境安装阶段在检出的仓库运行 `bash scripts/cloud/setup.sh`，安装依赖即可，不创建业务任务、不连接生产。
适配尚未合入 main 时，先核验环境检出的适配分支与交付 SHA，并在 start 命令追加 `--base <已核验适配提交SHA>`；合入 main 后恢复默认基座。
实际业务任务首次修改前运行 `scripts/task-worktree.sh start <slug> --development-only` 并切换到输出路径；继续任务复用原任务工作树。
新工作树不会继承虚拟环境或 node_modules，在该工作树运行安装入口。先读取已有配置，只在不存在时生成 backend/.env，不复制根目录或其他任务的 .env。

使用 `.venv/bin/python scripts/cloud/dev.py check` 检查已配置的独立开发数据库和 Redis；完整启动使用 `start`，只做前端使用 `start --frontend-only`。启动命令保持在任务终端会话内；以服务健康检查通过为就绪依据，结束时向该会话发送 Ctrl-C。不按进程名批量杀服务。

数据服务需要明确的开发资源；没有当前结构的脱敏开发数据库时，报告缺少的数据条件，可继续前端开发及 mock 单元测试，不自动迁移、导入生产快照或启用外部账号。
日志在忽略目录 `.cloud-dev/`，可能包含连接信息，禁止完整贴到对话或提交。

生产部署和验收关闭仅在本地进行。交付任务分支、已授权推送后的提交 SHA、变更与验证摘要、必要开发配置名称（不含值），按指南在本地接续同一任务。
