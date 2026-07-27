# TECH：企业管理整合组织管理

> 版本：V1.0 | 状态：实施 | 日期：2026-07-27  
> 依据：[REQ](./REQ_企业管理整合组织管理.md) · [UI](./UI_企业管理整合组织管理.md)

## 技术决策

采用仅前端整合方案，不修改数据库和后端协议：

- `/org-members/list` 继续作为全部正式成员和任职的权威查询。
- `/org-members/wecom-collected` 只提供企微关联用户集合，前端按 `user_id` 合并。
- 企微查询失败独立降级，不能覆盖或清空正式成员。
- 邀请、角色、显示名和任职分别复用现有治理接口。
- 管理后台 `tab` 与企业管理 `section` 使用 URL 查询参数，并验证当前用户可见性。
- 旧路由用 React Router `Navigate` 重定向，不保留旧弹窗运行链。

## 控制流

```text
/admin?tab=org&section=organization
  → AdminPanel 校验可见一级 Tab
  → OrgManagePanel 校验企业管理板块
  → OrganizationManageSection
      → MemberAssignmentsSection
          → 正式成员/部门/职位（必须成功）
          → 企微成员集合（允许独立失败）
      → GroupList
```

企业切换时以递增请求编号使旧响应失效，避免旧企业数据覆盖当前企业。

## 修改范围

- 路由：`frontend/src/App.tsx`、`frontend/src/pages/Admin.tsx`
- 一级与板块导航：`AdminPanel.tsx`、`OrgManagePanel.tsx`
- 组织管理：新增 `OrganizationManageSection.tsx`、`MemberManagementToolbar.tsx`、
  `MemberAssignmentEditor.tsx`
- 成员整合：扩展 `MemberAssignmentsSection.tsx`、`services/org.ts`
- 入口清理：`ChatHeader.tsx`
- 复用：`settings/GroupList.tsx`、`EditGroupNameModal.tsx`
- 删除：`OrganizationSettings.tsx`、`OrganizationModal.tsx`、`MemberList.tsx`、
  `EditMemberModal.tsx` 及旧成员列表测试。

## 失败与兼容

- 权威成员查询失败：错误 + 重试，不呈现空列表。
- 企微查询失败：成员可管理，显示企微状态未知。
- 保存失败：保留编辑态；后端仍执行最终权限和业务不变量检查。
- 显示名、角色和任职沿用三个既有写接口，不具备跨接口数据库事务；任一失败时保留
  编辑态供重试，重新加载后以后端权威状态为准。
- 非法或无权限 URL 参数：回退到第一个可见一级模块或组织管理默认板块。
- 回滚：恢复旧前端路由和组件即可；没有数据迁移或不可逆状态。

## 验证

- 管理后台深度链接和权限回退测试。
- 正式成员、企微成功/降级、角色修改测试。
- 群聊、凭证和 ChatHeader 回归测试。
- TypeScript 生产构建。
- `rg` 检查旧组件调用方归零，`wc -l` 检查结构阈值。
