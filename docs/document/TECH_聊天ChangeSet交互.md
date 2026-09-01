# 聊天 ChangeSet 交互技术记录

## 已确认的前置版本

- 第一批：`3abb4c2c`，当前基线合并提交为 `a981ea39`；提供 `changeset.v1` DTO、读取/时间线/取消/恢复 API 和内核状态。
- 第二批：当前仓库没有独立提交或可验证的冻结接口；因此本任务不调用未存在的 Plan Release、确认、试跑、审批或冲突解决后端接口。

## 职责边界

`ChangeSetCard.tsx` 只依赖第一批 `ChangeSet`/`ChangeSetTimeline` DTO。`changeSetResourceAdapters.ts` 是前端呈现适配器注册表，定时任务适配器仅负责字段摘要、Diff 标签和计划步骤解释，不拥有状态机。

`changeSetEvents.ts` 提供两个前端边界：状态变化通知会触发重新读取，动作请求交给第二批适配器或 WebSocket 入口。没有适配器时，确认/重新规划/解决冲突只发出事件并显示等待提示，不乐观修改状态。

`FormBlock` 通过 `change_set_id` 关联卡片；ChangeSet 创建成功后由服务端将该引用和表单完成快照持久化到消息，`applyFormSubmitResult` 只负责即时更新内存投影。消息刷新仍由既有消息 API 回读，卡片再按 ID 读取 ChangeSet。

## 验证与回滚

新增测试覆盖读取恢复、状态更新事件、取消防重、失败恢复、冲突提示、长文本/无 Diff、表单关联和旧表单兼容。生产灰度只需将 `VITE_CHANGESET_CHAT_UI` 设为 `false` 并重新加载前端；ChangeSet 和历史消息数据保留。

第二批接口已冻结后，聊天定时任务场景的冲突卡片会复用 `/scheduled-tasks/changesets` 生成新的幂等提案；通用卡片仍保留动作事件桥供后续资源适配器接入。创建草案 → 规划/只读试跑 → 确认 → applied，以及修改 → Diff → 版本冲突 → 基于最新版本重新规划/确认由定时任务适配器完成。
