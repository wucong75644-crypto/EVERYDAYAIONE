/**
 * ViewSwitcher — 视图切换器（按职位显示）
 *
 * - 老板/全公司副总: [全公司] [我的]
 * - 分管副总: [运营一部] [运营二部] [我的]
 * - 主管: [本部门] [我的]
 * - 员工/副主管: 不显示
 */
import { useAuthStore } from '../../stores/useAuthStore';
import { useScheduledTaskStore } from '../../stores/useScheduledTaskStore';
import { cn } from '../../utils/cn';

interface View {
  id: string;
  label: string;
  mode: 'default' | 'mine' | 'dept';
  deptId?: string;
}

function buildViews(currentOrg: NonNullable<ReturnType<typeof useAuthStore.getState>['user']>['current_org']): View[] {
  if (!currentOrg?.member) return [];

  const member = currentOrg.member;
  const views: View[] = [];

  if (
    member.position_code === 'boss' ||
    (member.position_code === 'vp' && member.data_scope === 'all')
  ) {
    views.push({ id: 'all', label: '全公司', mode: 'default' });
  } else if (member.position_code === 'vp' && member.managed_departments) {
    member.managed_departments.forEach((dept) => {
      views.push({
        id: `dept:${dept.id}`,
        label: dept.name,
        mode: 'dept',
        deptId: dept.id,
      });
    });
  } else if (member.position_code === 'manager' && member.department_id) {
    views.push({
      id: `dept:${member.department_id}`,
      label: member.department_name || '本部门',
      mode: 'dept',
      deptId: member.department_id,
    });
  }

  views.push({ id: 'mine', label: '我的', mode: 'mine' });

  return views;
}

export function ViewSwitcher() {
  const user = useAuthStore((s) => s.user);
  const viewMode = useScheduledTaskStore((s) => s.viewMode);
  const viewDeptId = useScheduledTaskStore((s) => s.viewDeptId);
  const setViewMode = useScheduledTaskStore((s) => s.setViewMode);

  const currentOrg = user?.current_org;
  if (!currentOrg) return null;

  const views = buildViews(currentOrg);
  if (views.length <= 1) return null;  // 员工/副主管不显示

  const currentViewId =
    viewMode === 'mine'
      ? 'mine'
      : viewMode === 'dept' && viewDeptId
        ? `dept:${viewDeptId}`
        : 'all';

  return (
    <div className="flex items-center gap-1 p-1 bg-[var(--s-surface-sunken)] rounded-lg mx-4 mt-3">
      {views.map((view) => {
        const isActive = view.id === currentViewId;
        return (
          <button
            key={view.id}
            type="button"
            onClick={() => setViewMode(view.mode, view.deptId)}
            className={cn(
              'flex-1 px-3 py-1.5 text-xs font-medium rounded-md transition-all',
              isActive
                ? 'bg-[var(--c-card-bg)] text-[var(--s-text-primary)] shadow-sm'
                : 'text-[var(--s-text-secondary)] hover:text-[var(--s-text-primary)]',
            )}
          >
            {view.label}
          </button>
        );
      })}
    </div>
  );
}
