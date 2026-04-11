/**
 * usePermission — 前端权限检查 hook
 *
 * 完全在前端运行，不调任何后端接口。
 * 数据来自 useAuthStore().user.current_org（来自 /api/auth/me 一次性返回）。
 *
 * 服务端会再校验一次（防绕过），前端只用于 UI 显示控制。
 *
 * 设计文档: docs/document/UI_定时任务面板设计.md §4.7
 */
import { useAuthStore } from '../stores/useAuthStore';
import type { CurrentMember } from '../types/auth';

interface ResourceLike {
  user_id: string;
  creator?: {
    department_id?: string | null;
  };
}

/**
 * 检查当前用户对某资源的权限
 *
 * @param permissionCode 权限码（如 'task.view', 'task.edit'）
 * @param resource 可选资源对象（含 user_id 创建者）
 * @returns 是否允许
 */
export function usePermission(
  permissionCode: string,
  resource?: ResourceLike,
): boolean {
  const user = useAuthStore((s) => s.user);
  const currentOrg = user?.current_org;

  if (!currentOrg || !user) return false;

  // 1. 检查功能权限码
  if (!currentOrg.permissions.includes(permissionCode)) {
    return false;
  }

  // 2. 没有 resource 参数 → 列表查询，由后端 SQL 注入处理
  if (!resource) return true;

  // 3. 检查数据范围
  const member = currentOrg.member;
  if (!member) return false;

  // 老板：全部允许
  if (member.position_code === 'boss') return true;

  // 全公司副总：全部允许
  if (member.position_code === 'vp' && member.data_scope === 'all') {
    return true;
  }

  // 分管副总：检查资源创建者是否在分管部门
  if (member.position_code === 'vp' && member.managed_departments) {
    const creatorDeptId = resource.creator?.department_id;
    return member.managed_departments.some((d) => d.id === creatorDeptId);
  }

  // 主管：本部门所有人
  if (member.position_code === 'manager') {
    const creatorDeptId = resource.creator?.department_id;
    return creatorDeptId === member.department_id;
  }

  // 副主管/员工：只能操作自己的资源
  return resource.user_id === user.id;
}

/**
 * 立即执行任务的特殊权限：员工/副主管不能强制执行别人的任务
 */
export function useCanExecuteTask(resource?: ResourceLike): boolean {
  const user = useAuthStore((s) => s.user);
  const member = user?.current_org?.member;
  const canExecute = usePermission('task.execute', resource);

  if (!member || !user) return false;

  // 老板/副总/主管：受 usePermission 数据范围约束
  if (['boss', 'vp', 'manager'].includes(member.position_code)) {
    return canExecute;
  }

  // 员工/副主管：只能执行自己的（且需要拥有 task.execute 权限）
  return canExecute && resource?.user_id === user.id;
}

/**
 * 获取当前成员的职位信息（便捷封装）
 */
export function useCurrentMember(): CurrentMember | null {
  return useAuthStore((s) => s.user?.current_org?.member ?? null);
}
