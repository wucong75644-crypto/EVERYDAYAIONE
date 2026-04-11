/**
 * 组织成员管理 Service
 *
 * 后端路由: backend/api/routes/org_members_assignments.py
 */
import api from './api';
import type {
  WecomCollectedMember,
  OrgDepartment,
  OrgPosition,
  UpdateAssignmentDto,
  UpdateProfileDto,
  MyMemberInfo,
} from '../types/orgMembers';

interface ApiResponse<T> {
  success: boolean;
  data: T;
  total?: number;
}

const BASE = '/org-members';

export const orgMembersService = {
  /** 当前用户在本企业的成员信息（任何成员都能调，不需要管理员） */
  async getMyMemberInfo(): Promise<MyMemberInfo> {
    const res = await api.get<ApiResponse<MyMemberInfo>>(`${BASE}/me`);
    return res.data.data;
  },

  /** 列出"和机器人聊过天"的员工（员工管理面板用） */
  async listWecomCollected(): Promise<WecomCollectedMember[]> {
    const res = await api.get<ApiResponse<WecomCollectedMember[]>>(`${BASE}/wecom-collected`);
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

  /** 修改成员部门/职位/数据范围 */
  async updateAssignment(userId: string, dto: UpdateAssignmentDto): Promise<void> {
    await api.patch(`${BASE}/${userId}/assignment`, dto);
  },

  /** 修改成员显示名（覆盖企微同步过来的） */
  async updateProfile(userId: string, dto: UpdateProfileDto): Promise<void> {
    await api.patch(`${BASE}/${userId}/profile`, dto);
  },
};
