/**
 * CreatorBadge — 任务创建者徽标
 *
 * 显示在主管/老板/副总视角的任务卡片底部：
 * 头像 + 姓名 + 部门徽标 + 职位徽标
 */
import type { ScheduledTaskCreator } from '../../types/scheduledTask';
import type { DepartmentType, PositionCode } from '../../types/auth';
import { cn } from '../../utils/cn';

interface Props {
  creator: ScheduledTaskCreator;
  className?: string;
}

const DEPT_COLORS: Record<DepartmentType, { bg: string; text: string; label: string }> = {
  ops:       { bg: '#dbeafe', text: '#1e40af', label: '运营' },
  finance:   { bg: '#d1fae5', text: '#065f46', label: '财务' },
  warehouse: { bg: '#fed7aa', text: '#9a3412', label: '仓库' },
  service:   { bg: '#e9d5ff', text: '#6b21a8', label: '客服' },
  design:    { bg: '#fce7f3', text: '#9f1239', label: '设计' },
  hr:        { bg: '#cffafe', text: '#155e75', label: '人事' },
  other:     { bg: '#f3f4f6', text: '#6b7280', label: '其他' },
};

const POSITION_COLORS: Record<PositionCode, { bg: string; text: string; label: string }> = {
  boss:    { bg: '#fef3c7', text: '#b45309', label: '老板' },
  vp:      { bg: '#f3f4f6', text: '#374151', label: '副总' },
  manager: { bg: '#dbeafe', text: '#1e3a8a', label: '主管' },
  deputy:  { bg: '#dbeafe', text: '#60a5fa', label: '副主管' },
  member:  { bg: '#f3f4f6', text: '#6b7280', label: '员工' },
};

function MiniBadge({ bg, text, label }: { bg: string; text: string; label: string }) {
  return (
    <span
      className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium"
      style={{ backgroundColor: bg, color: text }}
    >
      {label}
    </span>
  );
}

function Avatar({ name, src }: { name: string; src?: string | null }) {
  if (src) {
    return (
      <img
        src={src}
        alt={name}
        className="w-4 h-4 rounded-full object-cover shrink-0"
      />
    );
  }
  // 取首字
  const initial = name.charAt(0);
  return (
    <span className="w-4 h-4 rounded-full bg-[var(--s-accent-soft)] text-[var(--s-accent)] text-[10px] font-medium flex items-center justify-center shrink-0">
      {initial}
    </span>
  );
}

export function CreatorBadge({ creator, className }: Props) {
  if (!creator) return null;

  return (
    <div className={cn('flex items-center gap-1.5 text-xs flex-wrap', className)}>
      <Avatar name={creator.name} src={creator.avatar} />
      <span className="text-[var(--s-text-secondary)] truncate">{creator.name}</span>
      {creator.department_type && DEPT_COLORS[creator.department_type] && (
        <MiniBadge {...DEPT_COLORS[creator.department_type]} />
      )}
      {creator.position_code && POSITION_COLORS[creator.position_code] && (
        <MiniBadge {...POSITION_COLORS[creator.position_code]} />
      )}
    </div>
  );
}
