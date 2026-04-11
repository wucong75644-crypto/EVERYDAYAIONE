/**
 * MemberAssignmentsSection — 成员任职管理面板
 *
 * 老板/admin 编辑员工的部门、职位、数据范围。
 *
 * 设计文档: docs/document/TECH_组织架构与权限模型.md §九
 */
import { useEffect, useState, useCallback } from 'react';
import { Loader2, Edit2, Check, X, AlertCircle } from 'lucide-react';
import {
  orgMemberAssignmentService,
  type MemberWithAssignment,
  type OrgDepartment,
  type OrgPosition,
  type UpdateAssignmentDto,
} from '../../services/orgMemberAssignment';
import type { PositionCode, DataScope, DepartmentType } from '../../types/auth';
import { logger } from '../../utils/logger';
import { cn } from '../../utils/cn';

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

const DEPT_TYPE_LABELS: Record<DepartmentType, string> = {
  ops: '运营',
  finance: '财务',
  warehouse: '仓库',
  service: '客服',
  design: '设计',
  hr: '人事',
  other: '其他',
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
  const [editingUserId, setEditingUserId] = useState<string | null>(null);

  const loadAll = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [m, d, p] = await Promise.all([
        orgMemberAssignmentService.listMembers(),
        orgMemberAssignmentService.listDepartments(),
        orgMemberAssignmentService.listPositions(),
      ]);
      setMembers(m);
      setDepartments(d);
      setPositions(p);
    } catch (err: any) {
      logger.error('member-assignments', '加载失败', err);
      setError(err?.response?.data?.detail || '加载失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  // 未分配部门的成员数（横幅提示）
  const unassignedCount = members.filter(
    (m) => m.org_role !== 'owner' && !m.assignment?.department_id,
  ).length;

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
      {/* 未分配横幅 */}
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

      {/* 成员卡片列表 */}
      <div className="space-y-2">
        {members.map((m) => (
          <MemberRow
            key={m.user_id}
            member={m}
            departments={departments}
            positions={positions}
            isEditing={editingUserId === m.user_id}
            onStartEdit={() => setEditingUserId(m.user_id)}
            onCancelEdit={() => setEditingUserId(null)}
            onSaved={() => {
              setEditingUserId(null);
              loadAll();
            }}
          />
        ))}
      </div>
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
  isEditing: boolean;
  onStartEdit: () => void;
  onCancelEdit: () => void;
  onSaved: () => void;
}

function MemberRow({
  member,
  departments,
  positions,
  isEditing,
  onStartEdit,
  onCancelEdit,
  onSaved,
}: MemberRowProps) {
  const [deptId, setDeptId] = useState<string>(member.assignment?.department_id || '');
  const [posCode, setPosCode] = useState<PositionCode>(
    member.assignment?.position_code || 'member',
  );
  const [jobTitle, setJobTitle] = useState<string>(member.assignment?.job_title || '');
  const [dataScope, setDataScope] = useState<DataScope>(
    member.assignment?.data_scope || 'self',
  );
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  // 进入编辑模式时重置表单
  useEffect(() => {
    if (isEditing) {
      setDeptId(member.assignment?.department_id || '');
      setPosCode(member.assignment?.position_code || 'member');
      setJobTitle(member.assignment?.job_title || '');
      setDataScope(member.assignment?.data_scope || 'self');
      setSaveError(null);
    }
  }, [isEditing, member.assignment]);

  const handleSave = async () => {
    setSaving(true);
    setSaveError(null);
    try {
      const dto: UpdateAssignmentDto = {
        position_code: posCode,
        data_scope: dataScope,
        job_title: jobTitle.trim() || null,
      };
      // 只在职位非 boss/vp 时设置部门
      if (posCode !== 'boss' && posCode !== 'vp') {
        if (!deptId) {
          setSaveError('请选择部门');
          setSaving(false);
          return;
        }
        dto.department_id = deptId;
      }

      await orgMemberAssignmentService.updateAssignment(member.user_id, dto);
      onSaved();
    } catch (err: any) {
      setSaveError(err?.response?.data?.detail || '保存失败');
    } finally {
      setSaving(false);
    }
  };

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

  // 编辑态
  return (
    <div className="bg-surface rounded-lg p-4 border border-accent">
      <div className="flex items-center gap-3 mb-3">
        <Avatar name={member.nickname} src={member.avatar_url} />
        <span className="text-sm font-medium text-text-primary">{member.nickname}</span>
      </div>

      <div className="space-y-3">
        {/* 职位 */}
        <div>
          <label className="block text-xs font-medium text-text-secondary mb-1">职位</label>
          <select
            value={posCode}
            onChange={(e) => setPosCode(e.target.value as PositionCode)}
            className="w-full px-3 py-1.5 text-sm rounded border border-default bg-surface-card focus:outline-none focus:border-accent"
          >
            {positions.map((p) => (
              <option key={p.code} value={p.code}>
                {POSITION_LABELS[p.code]}
              </option>
            ))}
          </select>
        </div>

        {/* 部门（boss/vp 隐藏） */}
        {posCode !== 'boss' && posCode !== 'vp' && (
          <div>
            <label className="block text-xs font-medium text-text-secondary mb-1">部门 *</label>
            <select
              value={deptId}
              onChange={(e) => setDeptId(e.target.value)}
              className="w-full px-3 py-1.5 text-sm rounded border border-default bg-surface-card focus:outline-none focus:border-accent"
            >
              <option value="">请选择部门</option>
              {departments.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name} ({DEPT_TYPE_LABELS[d.type]})
                </option>
              ))}
            </select>
          </div>
        )}

        {/* 自定义头衔 */}
        <div>
          <label className="block text-xs font-medium text-text-secondary mb-1">
            自定义头衔（可选）
          </label>
          <input
            type="text"
            value={jobTitle}
            onChange={(e) => setJobTitle(e.target.value)}
            placeholder="如：高级运营专员"
            maxLength={50}
            className="w-full px-3 py-1.5 text-sm rounded border border-default bg-surface-card focus:outline-none focus:border-accent"
          />
        </div>

        {/* 数据范围 */}
        <div>
          <label className="block text-xs font-medium text-text-secondary mb-1">数据范围</label>
          <select
            value={dataScope}
            onChange={(e) => setDataScope(e.target.value as DataScope)}
            className="w-full px-3 py-1.5 text-sm rounded border border-default bg-surface-card focus:outline-none focus:border-accent"
          >
            <option value="self">仅自己</option>
            <option value="dept_subtree">本部门</option>
            <option value="all">全公司</option>
          </select>
          <p className="text-xs text-text-tertiary mt-1">
            决定该成员能查看多少数据范围
          </p>
        </div>

        {saveError && (
          <div className="text-xs text-error bg-error-light p-2 rounded">{saveError}</div>
        )}

        {/* 按钮 */}
        <div className="flex items-center justify-end gap-2 pt-2">
          <button
            onClick={onCancelEdit}
            className="px-3 py-1.5 text-xs rounded border border-default text-text-secondary hover:bg-hover transition-colors flex items-center gap-1"
          >
            <X className="w-3 h-3" />
            取消
          </button>
          <button
            onClick={handleSave}
            disabled={saving}
            className="px-3 py-1.5 text-xs rounded bg-accent text-text-on-accent hover:bg-accent-hover disabled:opacity-50 transition-colors flex items-center gap-1"
          >
            {saving ? <Loader2 className="w-3 h-3 animate-spin" /> : <Check className="w-3 h-3" />}
            {saving ? '保存中...' : '保存'}
          </button>
        </div>
      </div>
    </div>
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
