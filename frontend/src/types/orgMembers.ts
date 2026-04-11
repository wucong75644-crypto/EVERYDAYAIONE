/**
 * 组织成员管理类型定义
 *
 * 后端路由: backend/api/routes/org_members_assignments.py
 * 设计: docs/document/TECH_组织架构与权限模型.md §九
 */
import type { PositionCode, DepartmentType, DataScope } from './auth';

export interface MemberAssignment {
  department_id: string | null;
  department_name: string | null;
  department_type: DepartmentType | null;
  position_id: string | null;
  position_code: PositionCode | null;
  position_name: string | null;
  job_title: string | null;
  data_scope: DataScope;
  data_scope_dept_ids: string[];
}

/**
 * GET /api/org-members/wecom-collected 返回的单个员工
 *
 * 数据来源是 wecom_user_mappings（"和机器人聊过天"的子集）。
 * 没和机器人交互过的成员不在此列表，等他们首次发消息时自动收集。
 */
export interface WecomCollectedMember {
  user_id: string;
  nickname: string;
  avatar_url: string | null;
  // 企微相关
  wecom_userid: string | null;
  wecom_nickname: string | null;
  channel: string | null;
  last_chat_type: 'single' | 'group' | null;
  joined_at: string | null;
  // 任职信息（可能为 null：管理员还没分配过部门职位）
  assignment: MemberAssignment | null;
}

export interface OrgDepartment {
  id: string;
  name: string;
  type: DepartmentType;
  sort_order?: number;
}

export interface OrgPosition {
  id: string;
  code: PositionCode;
  name: string;
  level?: number;
}

// PATCH /api/org-members/{user_id}/assignment 请求体
export interface UpdateAssignmentDto {
  department_id?: string;
  position_code?: PositionCode;
  job_title?: string;
  data_scope?: DataScope;
  data_scope_dept_ids?: string[];
}

// PATCH /api/org-members/{user_id}/profile 请求体
export interface UpdateProfileDto {
  nickname: string;
}

/**
 * GET /api/org-members/me — 当前用户在企业内的精简信息
 *
 * 任何成员都能调，不需要管理员权限。
 * TaskForm 用 wecom_userid 构造"推送给自己"的 push_target。
 */
export interface MyMemberInfo {
  user_id: string;
  nickname: string;
  avatar_url: string | null;
  wecom_userid: string | null;
}
