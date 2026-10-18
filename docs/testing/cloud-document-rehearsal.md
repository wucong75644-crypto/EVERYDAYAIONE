# 云端独立任务文档演练

目的：验证真实独立云端工作树的创建、文档精确提交推送、最终提交 GitHub 检查与纯 rehearsal 流程。

流程：从最新 origin/main 使用 task-worktree.sh start cloud-document-rehearsal --development-only 创建任务，仅在任务分支继承已验证提交 40e252c6f8a4109626053f56e7d7f13c342bab97；通过 git-push.sh 只提交本文件，再执行 task_delivery.py rehearsal，由入口整合最新 main、等待最终 SHA 的 frontend/backend 检查并跟踪唯一演练 run。

边界：初始文档演练新增内容仅此文档，不修改业务、流水线或配置；不执行生产部署、验收、verification、真实 SSH，不读取生产凭据或连接生产数据库、Redis，不合并真实 main。生产开关状态不构成本次生产授权。完成后保留任务工作树和远程分支。

后续安全修复：真实发布子进程的 stdout/stderr 仅进入权限 0600 的临时文件，公开日志只输出严格校验后重建的结构化状态，失败保留非零退出；原始输出不上传 artifact、summary 或异常，临时文件随退出清理。本轮仅提交安全修复并等待完整 GitHub 检查，不部署或验收。当前生产待验收候选仍为 8fe7e7e62ea2aa186fb6c6e2b19de6514941283d；此修复 HEAD 尚未部署，须以后明确部署新 HEAD 并确认正常，才能验收清理。
