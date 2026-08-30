# 聊天 ChangeSet 交互

## 目标

聊天中的结构化表单只负责采集/修改草案；`ChangeSetCard` 负责读取并展示 ChangeSet 服务的真实状态、时间线、摘要、Diff、风险、权限、执行路径、检查结果和终态反馈。

消息只保存 `change_set_id`（以及可选的展示快照），刷新、切换对话和跨端恢复都通过 ChangeSet API 重新读取。表单没有 `change_set_id` 时保持旧流程，保证历史表单兼容。

## 交互状态

- `draft` 到 `awaiting_approval`：展示流程进度和可读检查结果；可取消。
- `awaiting_approval`：展示确认提交；确认动作由第二批适配器注入。
- `failed`：显示通用失败反馈，使用已存在的 recover API 重新生成草案。
- `conflicted`：固定提示“任务已被更新，请基于最新版本重新规划”，提供重新规划/解决冲突桥接动作。
- `applied`、`cancelled`、`rejected`、`expired`：展示不可误解的终态反馈，不再显示可提交按钮。

卡片动作按 ChangeSet ID 和当前 revision 发送到事件桥；提交中禁用全部按钮，重复点击不会产生第二次调用。

## 灰度与回退

前端开关：`VITE_CHANGESET_CHAT_UI`。默认开启；设置为 `false` 时带 ChangeSet 引用的表单也回退到旧表单展示和旧 `form_submit` 提交流程。旧消息和旧草案数据不删除。

通用卡片监听 `changeset:updated` 事件重新读取 DTO；当前已接入 `form_submit_result` 的 ChangeSet 引用通知。第二批冻结通用 ChangeSet WebSocket 事件名后，只需在 WebSocket 入口调用同一事件桥，无需改变卡片或另建定时任务状态机。

## 安全显示

卡片只显示白名单摘要字段和通用检查状态，过滤 token、secret、密码、凭证、SQL、堆栈等字段；底层错误码和内部异常不会展示给普通用户。
