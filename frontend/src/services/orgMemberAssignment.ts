/**
 * 组织成员任职管理 Service
 *
 * 后端路由: backend/api/routes/org_members_assignments.py
 */
import api from './api';
import type { PositionCode, DepartmentType, DataScope } from '../types/auth';

interface ApiResponse<T> {
  success: boolean;
  data: T;
}

export interface MemberAssignment {
  department_id?: string | null;
  department_name?: string | null;
  department_type?: DepartmentType | null;
  position_id?: string | null;
  position_code?: PositionCode | null;
  position_name?: string | null;
  job_title?: string | null;
  data_scope: DataScope;
  data_scope_dept_ids?: string[];
}

export interface MemberWithAssignment {
  user_id: string;
  nickname: string;
  avatar_url?: string | null;
  phone?: string | null;
  org_role: 'owner' | 'admin' | 'member';
  assignment: MemberAssignment | null;
}

export interface OrgDepartment {
  id: string;
  name: string;
  type: DepartmentType;
  sort_order: number;
}

export interface OrgPosition {
  id: string;
  code: PositionCode;
  name: string;
  level: number;
}

export interface UpdateAssignmentDto {
  department_id?: string | null;
  position_code?: PositionCode;
  job_title?: string | null;
  data_scope?: DataScope;
  data_scope_dept_ids?: string[];
}

const BASE = '/org-members';

export const orgMemberAssignmentService = {
  /** 列出所有成员（含部门/职位） */
  async listMembers(): Promise<MemberWithAssignment[]> {
    const res = await api.get<ApiResponse<MemberWithAssignment[]>>(`${BASE}/list`);
    return res.data.data;
  },

  /** 列出企业所有部门 */
  async listDepartments(): Promise<OrgDepartment[]> {
    const res = await api.get<ApiResponse<OrgDepartment[]>>(`${BASE}/departments`);
    return res.data.data;
  },

  /** 列出企业所有职位 */
  async listPositions(): Promise<OrgPosition[]> {
    const res = await api.get<ApiResponse<OrgPosition[]>>(`${BASE}/positions`);
    return res.data.data;
  },

  /** 修改成员任职 */
  async updateAssignment(userId: string, dto: UpdateAssignmentDto): Promise<void> {
    await api.patch(`${BASE}/${userId}/assignment`, dto);
  },
};
