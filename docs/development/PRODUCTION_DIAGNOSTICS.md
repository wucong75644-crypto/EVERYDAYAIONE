# 云端只读生产诊断

云端通过 GitHub 连接器读取并重新运行 `Read-only production diagnostics` 作业。
GitHub runner 使用 production 环境的 `DIAGNOSTICS_SSH_KEY`；云端不持有 SSH 密钥。
固定入口 `everydayai-diagnostics-v1` 由 root 所有的
`/usr/local/sbin/everydayai-diagnostics-ssh` 执行。
该密钥的 authorized_keys 必须使用 `restrict,command="/usr/local/sbin/everydayai-diagnostics-ssh"`。
现有发布网关、凭据和 policy.json 保持不变。

## 查询内容与边界

- 服务：everydayai-backend、everydayai-sync、everydayai-wecom、everydayai-conversation-actor、nginx。
- 状态：ActiveState、SubState、MainPID、NRestarts、ExecMainStatus。
- 每个服务最近 15 分钟最多 100 条 systemd journal 记录。
- 日志仅输出 UTC 时间、priority、固定错误类别、允许的异常类型、项目 Python 堆栈文件/行号、HTTP 状态。
- 不输出原始 MESSAGE、请求 URL/参数/正文、异常详情、源代码行、任意命令输出。
  未识别事件标为 unclassified，不能据此判断没有错误。
- 不读取应用 .env，不连接数据库/Redis，不重启、不部署。
- nginx 此入口仅查其 journal，不覆盖 nginx 独立 access/error 文件。
- 日志为空或 query failed 必须如实报告；此投影可定位部分错误，不代替所有故障的完整现场。

## 发起与复用

首次在已推送诊断任务提交上创建 `delivery-verification/diagnostics-<标识>/<nonce>` 标签。
只有 `production-diagnostics.yml` 执行诊断；旧凭据验证 job 对此标签跳过。
production 环境沿用已审核的 `delivery-verification/*/*` 标签策略和请求账号约束。
诊断成功后记录 run/job 链接。云端查询新现场时：

1. 仅重新运行这个固定诊断 job（不重跑任何发布 job）。
2. 等待新 attempt 完成，读取新 job 的日志中 `DIAGNOSTICS_RESULT` JSON。
3. 引用 collected_at、run attempt 和服务，避免把旧数据当实时状态。
4. 结合仓库内对应生产版本代码和错误堆栈定位问题；证据不足时说明缺口，不猜测。

原始 journal 在服务器端转换；runner 仅输出哈希验证通过的投影。
GitHub 作业日志可能按仓库可见性公开，因此不能把原始日志改为直接打印。
诊断凭据是为此用途新建的专用密钥，绝不复制既有本机管理私钥。
撤销：删除 authorized_keys 中 `everydayai-readonly-diagnostics` 标记的公钥行，
删除 GitHub production 环境的 DIAGNOSTICS_SSH_KEY；诊断脚本可保留或单独移除。
此操作不影响现有发布身份。网关安装时保存 authorized_keys 备份供必要时恢复；
如有并发新增密钥，不要用整文件旧备份覆盖，应只删除本任务标记行。

本功能属于运维配置，不部署应用、不更新发布候选、不合并 main、不清理任务工作树。
