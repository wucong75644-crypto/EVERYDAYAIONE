# 云端独立任务文档演练

目的：验证真实独立云端工作树的创建、文档精确提交推送、最终提交 GitHub 检查与纯 rehearsal 流程。

流程：从最新 origin/main 使用 task-worktree.sh start cloud-document-rehearsal --development-only 创建任务，仅在任务分支继承已验证提交 40e252c6f8a4109626053f56e7d7f13c342bab97；通过 git-push.sh 只提交本文件，再执行 task_delivery.py rehearsal，由入口整合最新 main、等待最终 SHA 的 frontend/backend 检查并跟踪唯一演练 run。

边界：本次新增内容仅此文档，不修改业务、流水线或配置；不执行生产部署、验收、verification、真实 SSH，不读取生产凭据或连接生产数据库、Redis，不合并真实 main。生产开关状态不构成本次生产授权。完成后保留任务工作树和远程分支。
