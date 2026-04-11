/**
 * 员工列表 — 显示和机器人交互过的员工
 *
 * 列：真名 / 部门 / 职位 / 数据范围 / 最后活跃 / [编辑]
 * 点击"编辑" → 弹出 EditMemberModal 修改部门/职位/显示名
 *
 * 数据来源：GET /api/org-members/wecom-collected
 * 不包含没和机器人聊过的成员（按用户需求）
 */
import { useEffect, useState, useCallback } from 'react';
import { Loader2, Pencil, RefreshCw } from 'lucide-react';
import { orgMembersService } from '../../services/orgMembers';
import { logger } from '../../utils/logger';
import { cn } from '../../utils/cn';
import EditMemberModal from './EditMemberModal';
import type {
  WecomCollectedMember,
  OrgDepartment,
  OrgPosition,
} from '../../types/orgMembers';

const POSITION_LABELS: Record<string, string> = {
  boss: '老板',
  vp: '副总',
  manager: '主管',
  deputy: '副主管',
  member: '员工',
};

const SCOPE_LABELS: Record<string, string> = {
  all: '全公司',
  dept_subtree: '本部门',
  self: '仅自己',
};

function formatTime(iso: string | null): string {
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    const now = new Date();
    const diffMs = now.getTime() - d.getTime();
    const diffHours = diffMs / (1000 * 60 * 60);
    const diffDays = diffHours / 24;

    if (diffHours < 1) return '刚刚';
    if (diffHours < 24) return `${Math.floor(diffHours)} 小时前`;
    if (diffDays < 7) return `${Math.floor(diffDays)} 天前`;
    return d.toLocaleDateString('zh-CN');
  } catch {
    return iso;
  }
}

export default function MemberList() {
  const [members, setMembers] = useState<WecomCollectedMember[]>([]);
  const [departments, setDepartments] = useState<OrgDepartment[]>([]);
  const [positions, setPositions] = useState<OrgPosition[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<WecomCollectedMember | null>(null);

  const loadAll = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [m, d, p] = await Promise.all([
        orgMembersService.listWecomCollected(),
        orgMembersService.listDepartments(),
        orgMembersService.listPositions(),
      ]);
      setMembers(m);
      setDepartments(d);
      setPositions(p);
    } catch (e) {
      logger.error('member-list', '加载失败', e);
      setError('加载失败，请重试');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  const handleSaved = () => {
    setEditing(null);
    loadAll();
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-16 text-[var(--s-text-tertiary)]">
        <Loader2 className="w-5 h-5 animate-spin mr-2" />
        加载中...
      </div>
    );
  }

  if (error) {
    return (
      <div className="text-center py-16">
        <p className="text-[var(--s-error)] mb-3">{error}</p>
        <button
          type="button"
          onClick={loadAll}
          className="text-sm text-[var(--s-accent)] hover:underline"
        >
          重试
        </button>
      </div>
    );
  }

  if (members.length === 0) {
    return (
      <div className="text-center py-16 text-[var(--s-text-tertiary)] text-sm">
        还没有员工和机器人聊过天<br />
        <span className="text-xs">员工首次给机器人发消息后会自动出现在此</span>
      </div>
    );
  }

  return (
    <div>
      {/* 工具栏 */}
      <div className="flex items-center justify-between mb-3 px-1">
        <span className="text-xs text-[var(--s-text-tertiary)]">
          共 {members.length} 名员工（仅显示和机器人交互过的）
        </span>
        <button
          type="button"
          onClick={loadAll}
          className="flex items-center gap-1 text-xs text-[var(--s-text-secondary)] hover:text-[var(--s-text-primary)]"
        >
          <RefreshCw className="w-3 h-3" />
          刷新
        </button>
      </div>

      {/* 表格 */}
      <div className="border border-[var(--s-border-default)] rounded-md overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-[var(--s-surface-sunken)] text-[var(--s-text-tertiary)] text-xs">
            <tr>
              <th className="text-left px-3 py-2 font-medium">姓名</th>
              <th className="text-left px-3 py-2 font-medium">部门</th>
              <th className="text-left px-3 py-2 font-medium">职位</th>
              <th className="text-left px-3 py-2 font-medium">数据范围</th>
              <th className="text-left px-3 py-2 font-medium">加入</th>
              <th className="text-right px-3 py-2 font-medium w-16">操作</th>
            </tr>
          </thead>
          <tbody>
            {members.map((m) => (
              <tr
                key={m.user_id}
                className="border-t border-[var(--s-border-default)] hover:bg-[var(--s-hover)]"
              >
                <td className="px-3 py-2.5">
                  <div className="flex items-center gap-2">
                    {m.avatar_url ? (
                      <img
                        src={m.avatar_url}
                        alt=""
                        className="w-6 h-6 rounded-full"
                      />
                    ) : (
                      <div className="w-6 h-6 rounded-full bg-[var(--s-surface-sunken)] flex items-center justify-center text-[10px] text-[var(--s-text-tertiary)]">
                        {m.nickname.charAt(0)}
                      </div>
                    )}
                    <span className="text-[var(--s-text-primary)] font-medium">
                      {m.nickname}
                    </span>
                  </div>
                </td>
                <td className="px-3 py-2.5 text-[var(--s-text-secondary)]">
                  {m.assignment?.department_name || (
                    <span className="text-[var(--s-text-tertiary)]">未分配</span>
                  )}
                </td>
                <td className="px-3 py-2.5 text-[var(--s-text-secondary)]">
                  {m.assignment?.position_code
                    ? POSITION_LABELS[m.assignment.position_code]
                    : <span className="text-[var(--s-text-tertiary)]">未分配</span>}
                </td>
                <td className="px-3 py-2.5 text-[var(--s-text-secondary)]">
                  {m.assignment?.data_scope
                    ? SCOPE_LABELS[m.assignment.data_scope]
                    : <span className="text-[var(--s-text-tertiary)]">—</span>}
                </td>
                <td className="px-3 py-2.5 text-[var(--s-text-tertiary)] text-xs">
                  {formatTime(m.joined_at)}
                </td>
                <td className="px-3 py-2.5 text-right">
                  <button
                    type="button"
                    onClick={() => setEditing(m)}
                    className={cn(
                      'inline-flex items-center gap-1 px-2 py-1 rounded',
                      'text-xs text-[var(--s-text-secondary)]',
                      'hover:bg-[var(--s-accent-soft)] hover:text-[var(--s-accent)]',
                      'transition-colors',
                    )}
                  >
                    <Pencil className="w-3 h-3" />
                    编辑
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* 编辑子 Modal */}
      {editing && (
        <EditMemberModal
          member={editing}
          departments={departments}
          positions={positions}
          onClose={() => setEditing(null)}
          onSaved={handleSaved}
        />
      )}
    </div>
  );
}
