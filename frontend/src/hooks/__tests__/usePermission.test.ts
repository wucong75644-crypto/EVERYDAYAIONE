/**
 * usePermission Hook 测试
 *
 * 覆盖 5 个职位 × 数据范围矩阵
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { renderHook } from '@testing-library/react';
import { usePermission, useCanExecuteTask } from '../usePermission';
import { useAuthStore } from '../../stores/useAuthStore';
import type { User, CurrentMember } from '../../types/auth';

function setUser(member: CurrentMember | null, permissions: string[] = [], userId = 'user_zhangsan') {
  const user: User = {
    id: userId,
    nickname: '张三',
    avatar_url: null,
    phone: null,
    role: 'user',
    credits: 100,
    created_at: '2026-04-01T00:00:00Z',
    current_org: {
      id: 'org_lanchuang',
      name: '蓝创科技',
      role: 'member',
      member,
      permissions,
    },
  };
  useAuthStore.setState({ user });
}

beforeEach(() => {
  useAuthStore.setState({ user: null });
});

describe('usePermission - 没有权限码', () => {
  it('user 为 null 时返回 false', () => {
    const { result } = renderHook(() => usePermission('task.view'));
    expect(result.current).toBe(false);
  });

  it('权限码不在列表中时返回 false', () => {
    setUser(
      { position_code: 'member', data_scope: 'self' },
      ['task.view'],
    );
    const { result } = renderHook(() => usePermission('task.delete'));
    expect(result.current).toBe(false);
  });
});

describe('usePermission - 老板（boss）', () => {
  it('有权限码 + 任意 resource → 允许', () => {
    setUser(
      { position_code: 'boss', data_scope: 'all' },
      ['task.view', 'task.edit', 'task.delete'],
    );
    const { result } = renderHook(() =>
      usePermission('task.edit', {
        user_id: 'other_user',
        creator: { department_id: 'dept_xyz' },
      }),
    );
    expect(result.current).toBe(true);
  });
});

describe('usePermission - 全公司副总（vp + all）', () => {
  it('任意 resource → 允许', () => {
    setUser(
      { position_code: 'vp', data_scope: 'all' },
      ['task.view', 'task.edit'],
    );
    const { result } = renderHook(() =>
      usePermission('task.edit', {
        user_id: 'other',
        creator: { department_id: 'dept_xyz' },
      }),
    );
    expect(result.current).toBe(true);
  });
});

describe('usePermission - 分管副总（vp + dept_subtree）', () => {
  it('分管部门内的资源 → 允许', () => {
    setUser(
      {
        position_code: 'vp',
        data_scope: 'dept_subtree',
        managed_departments: [
          { id: 'dept_ops_1', name: '运营一部' },
          { id: 'dept_ops_2', name: '运营二部' },
        ],
      },
      ['task.view', 'task.edit'],
    );
    const { result } = renderHook(() =>
      usePermission('task.edit', {
        user_id: 'other',
        creator: { department_id: 'dept_ops_1' },
      }),
    );
    expect(result.current).toBe(true);
  });

  it('其他部门的资源 → 拒绝', () => {
    setUser(
      {
        position_code: 'vp',
        data_scope: 'dept_subtree',
        managed_departments: [{ id: 'dept_ops_1', name: '运营一部' }],
      },
      ['task.view', 'task.edit'],
    );
    const { result } = renderHook(() =>
      usePermission('task.edit', {
        user_id: 'other',
        creator: { department_id: 'dept_finance' },
      }),
    );
    expect(result.current).toBe(false);
  });
});

describe('usePermission - 主管（manager）', () => {
  it('本部门的资源 → 允许', () => {
    setUser(
      {
        position_code: 'manager',
        department_id: 'dept_ops_1',
        department_type: 'ops',
        data_scope: 'dept_subtree',
      },
      ['task.view', 'task.edit'],
    );
    const { result } = renderHook(() =>
      usePermission('task.edit', {
        user_id: 'other',
        creator: { department_id: 'dept_ops_1' },
      }),
    );
    expect(result.current).toBe(true);
  });

  it('其他部门的资源 → 拒绝', () => {
    setUser(
      {
        position_code: 'manager',
        department_id: 'dept_ops_1',
        department_type: 'ops',
        data_scope: 'dept_subtree',
      },
      ['task.view', 'task.edit'],
    );
    const { result } = renderHook(() =>
      usePermission('task.edit', {
        user_id: 'other',
        creator: { department_id: 'dept_ops_2' },
      }),
    );
    expect(result.current).toBe(false);
  });
});

describe('usePermission - 员工（member）', () => {
  it('自己创建的资源 → 允许', () => {
    setUser(
      {
        position_code: 'member',
        department_id: 'dept_ops_1',
        department_type: 'ops',
        data_scope: 'self',
      },
      ['task.view', 'task.edit'],
    );
    const { result } = renderHook(() =>
      usePermission('task.edit', {
        user_id: 'user_zhangsan',
        creator: { department_id: 'dept_ops_1' },
      }),
    );
    expect(result.current).toBe(true);
  });

  it('别人的资源（同部门同事） → 拒绝', () => {
    setUser(
      {
        position_code: 'member',
        department_id: 'dept_ops_1',
        department_type: 'ops',
        data_scope: 'self',
      },
      ['task.view', 'task.edit'],
    );
    const { result } = renderHook(() =>
      usePermission('task.edit', {
        user_id: 'user_lisi',
        creator: { department_id: 'dept_ops_1' },
      }),
    );
    expect(result.current).toBe(false);
  });
});

describe('usePermission - 副主管（deputy）', () => {
  it('副主管 = 员工权限：只能操作自己', () => {
    setUser(
      {
        position_code: 'deputy',
        department_id: 'dept_ops_1',
        department_type: 'ops',
        data_scope: 'self',
      },
      ['task.view', 'task.edit'],
    );
    const own = renderHook(() =>
      usePermission('task.edit', { user_id: 'user_zhangsan' }),
    );
    const other = renderHook(() =>
      usePermission('task.edit', { user_id: 'user_other' }),
    );
    expect(own.result.current).toBe(true);
    expect(other.result.current).toBe(false);
  });
});

describe('useCanExecuteTask', () => {
  it('员工只能执行自己的任务', () => {
    setUser(
      {
        position_code: 'member',
        department_id: 'dept_ops_1',
        department_type: 'ops',
        data_scope: 'self',
      },
      ['task.view', 'task.execute'],
    );
    const own = renderHook(() => useCanExecuteTask({ user_id: 'user_zhangsan' }));
    const other = renderHook(() => useCanExecuteTask({ user_id: 'user_other' }));
    expect(own.result.current).toBe(true);
    expect(other.result.current).toBe(false);
  });

  it('主管能执行本部门成员的任务', () => {
    setUser(
      {
        position_code: 'manager',
        department_id: 'dept_ops_1',
        department_type: 'ops',
        data_scope: 'dept_subtree',
      },
      ['task.view', 'task.execute'],
    );
    const { result } = renderHook(() =>
      useCanExecuteTask({
        user_id: 'user_other',
        creator: { department_id: 'dept_ops_1' },
      }),
    );
    expect(result.current).toBe(true);
  });
});
