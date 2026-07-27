# REQ：企业管理整合组织管理

> 版本：V1.0 | 状态：已确认 | 日期：2026-07-27

## 已确认目标

- 保留管理后台一级模块“企业管理”，“组织管理”作为其内部板块。
- 移除聊天顶部独立组织管理入口，旧 `/settings/organization` 保留兼容跳转。
- 将成员管理、部门职位和企微员工管理合并为“成员与任职”。
- 以全部有效企业成员为权威列表，企微交互状态作为附加状态和筛选条件。
- 邀请、企业角色、企业内显示名、部门、职位和数据范围在同一成员上下文管理。
- 群聊管理并入组织管理，但与企业微信凭证配置保持独立。
- 企业信息、企业微信、ERP 凭证和 AI 配置继续作为企业管理的独立板块。
- 权限保持为当前企业 `owner/admin`，不扩大主管或普通成员权限。

## 成功条件

1. 管理员只通过“管理后台 → 企业管理 → 组织管理”完成成员、任职和群聊管理。
2. 同一成员不再出现在三套含义重叠的界面。
3. 企微状态失败不会阻断正式成员和任职管理，也不会把未知误标为未关联。
4. 旧组织管理地址进入新板块，旧弹窗不再加载。
5. 后端权限和现有数据模型不变。

## 排除项

- 不修改数据库 Schema 或后端 API 协议。
- 不新增管理角色或权限。
- 不重做 ERP、AI、企微凭证配置流程。

## 事实来源

- `frontend/src/components/admin/OrgManagePanel.tsx`
- `frontend/src/components/admin/MemberAssignmentsSection.tsx`
- `frontend/src/components/settings/OrganizationModal.tsx`（整合前）
- `backend/api/routes/org_members_assignments.py`
- `backend/api/routes/wecom_chat_targets.py`
- `backend/migrations/194_governed_assignment_management.sql`
