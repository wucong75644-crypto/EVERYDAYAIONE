/**
 * MemberAssignmentsSection — 成员任职管理面板
 *
 * 老板/admin 编辑员工的部门、职位、数据范围。
 *
 * 设计文档: docs/document/TECH_组织架构与权限模型.md §九
 */
import { useEffect, useState, useCallback, useRef } from 'react';
import { Loader2, Edit2, AlertCircle } from 'lucide-react';
import {
  orgMemberAssignmentService,
  type MemberWithAssignment,
  type OrgDepartment,
  type OrgPosition,
} from '../../services/orgMemberAssignment';
import { orgMembersService } from '../../services/orgMembers';
import { useAuthStore } from '../../stores/useAuthStore';
import type { PositionCode, DataScope } from '../../types/auth';
import { logger } from '../../utils/logger';
import { cn } from '../../utils/cn';
import MemberManagementToolbar from './MemberManagementToolbar';
import MemberAssignmentEditor from './MemberAssignmentEditor';

interface Props {
  orgId: string;
}

const POSITION_LABELS: Record<PositionCode, string> = {
  boss: '老板',
  vp: '副总',
  manager: '主管',
  deputy: '副主管',
  member: '员工',
};

const DATA_SCOPE_LABELS: Record<DataScope, string> = {
  all: '全公司',
  dept_subtree: '本部门',
  self: '仅自己',
};

export function MemberAssignmentsSection({ orgId: _orgId }: Props) {
  const [members, setMembers] = useState<MemberWithAssignment[]>([]);
  const [departments, setDepartments] = useState<OrgDepartment[]>([]);
  const [positions, setPositions] = useState<OrgPosition[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [wecomUserIds, setWecomUserIds] = useState<Set<string> | null>(null);
  const [wecomError, setWecomError] = useState(false);
  const [wecomFilter, setWecomFilter] = useState<'all' | 'linked' | 'unlinked'>('all');
  const [editingUserId, setEditingUserId] = useState<string | null>(null);
  const loadRequest = useRef(0);
  const currentOrgRole = useAuthStore((state) => state.currentOrg?.role);

  const loadAll = useCallback(async () => {
    void _orgId;
    const requestId = ++loadRequest.current;
    setLoading(true);
    setError(null);
    const wecomRequest = orgMembersService.listWecomCollected()
      .then((items) => ({ items, failed: false }))
      .catch(() => ({ items: [], failed: true }));
    try {
      const [m, d, p] = await Promise.all([
        orgMemberAssignmentService.listMembers(),
        orgMemberAssignmentService.listDepartments(),
        orgMemberAssignmentService.listPositions(),
      ]);
      if (requestId !== loadRequest.current) return;
      setMembers(m);
      setDepartments(d);
      setPositions(p);
      setLoading(false);

      const collected = await wecomRequest;
      if (requestId !== loadRequest.current) return;
      if (collected.failed) {
        setWecomUserIds(null);
        setWecomError(true);
      } else {
        setWecomUserIds(new Set(collected.items.map((member) => member.user_id)));
        setWecomError(false);
      }
    } catch (err: unknown) {
      if (requestId !== loadRequest.current) return;
      logger.error('member-assignments', '加载失败', err);
      setError('加载失败');
    } finally {
      if (requestId === loadRequest.current) setLoading(false);
    }
  }, [_orgId]);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  // 未分配部门的成员数（横幅提示）
  const unassignedCount = members.filter(
    (m) => m.org_role !== 'owner' && !m.assignment?.department_id,
  ).length;
  const visibleMembers = members.filter((member) => {
    if (wecomFilter === 'all' || !wecomUserIds) return true;
    const linked = wecomUserIds.has(member.user_id);
    return wecomFilter === 'linked' ? linked : !linked;
  });

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12 text-text-tertiary">
        <Loader2 className="w-5 h-5 animate-spin mr-2" />
        加载中...
      </div>
    );
  }

  if (error) {
    return (
      <div className="bg-error-light text-error p-3 rounded-lg text-sm">
        {error}
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <MemberManagementToolbar
        orgId={_orgId}
        memberCount={members.length}
        filter={wecomFilter}
        filterDisabled={!wecomUserIds}
        onFilterChange={setWecomFilter}
      />
      <AssignmentWarnings wecomError={wecomError} unassignedCount={unassignedCount} />
      <MemberRows
        members={visibleMembers}
        departments={departments}
        positions={positions}
        orgId={_orgId}
        canChangeRole={currentOrgRole === 'owner'}
        wecomUserIds={wecomUserIds}
        editingUserId={editingUserId}
        onEdit={setEditingUserId}
        onReload={loadAll}
      />
      {visibleMembers.length === 0 && (
        <div className="text-center text-text-tertiary py-8">
          {members.length === 0 ? '企业暂无有效成员' : '当前筛选没有匹配的成员'}
        </div>
      )}
    </div>
  );
}

function AssignmentWarnings({
  wecomError,
  unassignedCount,
}: {
  wecomError: boolean;
  unassignedCount: number;
}) {
  return (
    <>
      {wecomError && (
        <div className="bg-warning-light text-warning p-3 rounded-lg text-sm">
          企微关联状态暂不可用，成员与任职信息仍可正常管理
        </div>
      )}
      {unassignedCount > 0 && (
        <div className="bg-warning-light text-warning p-3 rounded-lg text-sm flex items-start gap-2">
          <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />
          <div>
            <strong>{unassignedCount} 名成员未分配部门</strong>
            <p className="text-xs mt-0.5 opacity-80">
              未分配部门的成员只能看自己的数据，请尽快分配
            </p>
          </div>
        </div>
      )}
    </>
  );
}

function MemberRows({
  members,
  departments,
  positions,
  orgId,
  canChangeRole,
  wecomUserIds,
  editingUserId,
  onEdit,
  onReload,
}: {
  members: MemberWithAssignment[];
  departments: OrgDepartment[];
  positions: OrgPosition[];
  orgId: string;
  canChangeRole: boolean;
  wecomUserIds: Set<string> | null;
  editingUserId: string | null;
  onEdit: (userId: string | null) => void;
  onReload: () => void;
}) {
  return (
    <div className="space-y-2">
      {members.map((member) => (
        <MemberRow
          key={member.user_id}
          member={member}
          departments={departments}
          positions={positions}
          orgId={orgId}
          canChangeRole={canChangeRole}
          wecomLinked={wecomUserIds?.has(member.user_id)}
          isEditing={editingUserId === member.user_id}
          onStartEdit={() => onEdit(member.user_id)}
          onCancelEdit={() => onEdit(null)}
          onSaved={() => {
            onEdit(null);
            onReload();
          }}
        />
      ))}
    </div>
  );
}

// ════════════════════════════════════════════════════════
// MemberRow — 单个成员行
// ════════════════════════════════════════════════════════

interface MemberRowProps {
  member: MemberWithAssignment;
  departments: OrgDepartment[];
  positions: OrgPosition[];
  orgId: string;
  canChangeRole: boolean;
  wecomLinked: boolean | undefined;
  isEditing: boolean;
  onStartEdit: () => void;
  onCancelEdit: () => void;
  onSaved: () => void;
}

function MemberRow({
  member,
  departments,
  positions,
  orgId,
  canChangeRole,
  wecomLinked,
  isEditing,
  onStartEdit,
  onCancelEdit,
  onSaved,
}: MemberRowProps) {
  // 显示态
  if (!isEditing) {
    const a = member.assignment;
    const isOwner = member.org_role === 'owner';

    return (
      <div className="flex items-center justify-between p-3 bg-surface rounded-lg group">
        <div className="flex items-center gap-3 min-w-0 flex-1">
          <Avatar name={member.nickname} src={member.avatar_url} />
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium text-text-primary">{member.nickname}</span>
              {isOwner && (
                <span className="text-[10px] px-1.5 py-0.5 bg-warning-light text-warning rounded">
                  老板
                </span>
              )}
              {!isOwner && (
                <span className="text-[10px] px-1.5 py-0.5 bg-surface-sunken text-text-secondary rounded">
                  {member.org_role === 'admin' ? '管理员' : '成员'}
                </span>
              )}
              <span
                className={`text-[10px] px-1.5 py-0.5 rounded ${
                  wecomLinked === true
                    ? 'bg-success-light text-success'
                    : 'bg-surface-sunken text-text-tertiary'
                }`}
              >
                {wecomLinked === undefined
                  ? '企微状态未知'
                  : wecomLinked
                    ? '已关联企微'
                    : '未关联企微'}
              </span>
            </div>
            <div className="text-xs text-text-tertiary mt-0.5 flex flex-wrap items-center gap-1.5">
              {a?.department_name && (
                <span className="px-1.5 py-0.5 bg-accent-light text-accent rounded text-[10px]">
                  {a.department_name}
                </span>
              )}
              {a?.position_code && (
                <span className="px-1.5 py-0.5 bg-surface-sunken text-text-secondary rounded text-[10px]">
                  {POSITION_LABELS[a.position_code]}
                </span>
              )}
              {a?.job_title && <span className="text-text-tertiary">{a.job_title}</span>}
              {!a?.department_id && !isOwner && (
                <span className="text-warning">⚠ 未分配部门</span>
              )}
              <span className="text-text-tertiary">·</span>
              <span>数据范围: {DATA_SCOPE_LABELS[a?.data_scope || 'self']}</span>
            </div>
          </div>
        </div>
        <button
          onClick={onStartEdit}
          className={cn(
            'p-1.5 rounded text-text-tertiary',
            'hover:bg-hover hover:text-text-primary',
            'opacity-0 group-hover:opacity-100 transition-opacity',
          )}
          title="编辑任职"
        >
          <Edit2 className="w-4 h-4" />
        </button>
      </div>
    );
  }

  return (
    <MemberAssignmentEditor
      member={member}
      departments={departments}
      positions={positions}
      orgId={orgId}
      canChangeRole={canChangeRole}
      onCancel={onCancelEdit}
      onSaved={onSaved}
    />
  );
}


function Avatar({ name, src }: { name: string; src?: string | null }) {
  if (src) {
    return <img src={src} alt={name} className="w-8 h-8 rounded-full object-cover shrink-0" />;
  }
  return (
    <span className="w-8 h-8 rounded-full bg-accent-light text-accent text-sm font-medium flex items-center justify-center shrink-0">
      {name[0] || '?'}
    </span>
  );
}
