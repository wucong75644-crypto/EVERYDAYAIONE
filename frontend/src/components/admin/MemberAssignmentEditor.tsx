import { useState } from 'react';
import { Check, Loader2, X } from 'lucide-react';
import {
  orgMemberAssignmentService,
  type MemberWithAssignment,
  type OrgDepartment,
  type OrgPosition,
  type UpdateAssignmentDto,
} from '../../services/orgMemberAssignment';
import { orgMembersService } from '../../services/orgMembers';
import { changeMemberRole } from '../../services/org';
import type { DataScope, DepartmentType, PositionCode } from '../../types/auth';

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

interface Props {
  member: MemberWithAssignment;
  departments: OrgDepartment[];
  positions: OrgPosition[];
  orgId: string;
  canChangeRole: boolean;
  onCancel: () => void;
  onSaved: () => void;
}

export default function MemberAssignmentEditor({
  member,
  departments,
  positions,
  orgId,
  canChangeRole,
  onCancel,
  onSaved,
}: Props) {
  const [deptId, setDeptId] = useState(member.assignment?.department_id || '');
  const [posCode, setPosCode] = useState<PositionCode>(
    member.assignment?.position_code || 'member',
  );
  const [jobTitle, setJobTitle] = useState(member.assignment?.job_title || '');
  const [nickname, setNickname] = useState(member.nickname);
  const [orgRole, setOrgRole] = useState<'admin' | 'member'>(
    member.org_role === 'admin' ? 'admin' : 'member',
  );
  const [dataScope, setDataScope] = useState<DataScope>(
    member.assignment?.data_scope || 'self',
  );
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const save = async () => {
    const dto: UpdateAssignmentDto = {
      position_code: posCode,
      data_scope: dataScope,
      job_title: jobTitle.trim() || null,
    };
    if (posCode !== 'boss' && posCode !== 'vp') {
      if (!deptId) {
        setSaveError('请选择部门');
        return;
      }
      dto.department_id = deptId;
    }

    setSaving(true);
    setSaveError(null);
    try {
      const updates: Promise<unknown>[] = [
        orgMemberAssignmentService.updateAssignment(member.user_id, dto),
      ];
      if (nickname.trim() && nickname.trim() !== member.nickname) {
        updates.push(orgMembersService.updateProfile(member.user_id, {
          nickname: nickname.trim(),
        }));
      }
      if (canChangeRole && orgRole !== member.org_role) {
        updates.push(changeMemberRole(orgId, member.user_id, orgRole));
      }
      await Promise.all(updates);
      onSaved();
    } catch {
      setSaveError('保存失败，请重试');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="bg-surface rounded-lg p-4 border border-accent space-y-3">
      <MemberEditorFields
        nickname={nickname}
        onNicknameChange={setNickname}
        showRole={canChangeRole && member.org_role !== 'owner'}
        orgRole={orgRole}
        onRoleChange={setOrgRole}
        posCode={posCode}
        onPositionChange={setPosCode}
        positions={positions}
        deptId={deptId}
        onDepartmentChange={setDeptId}
        departments={departments}
        jobTitle={jobTitle}
        onJobTitleChange={setJobTitle}
        dataScope={dataScope}
        onDataScopeChange={setDataScope}
      />
      {saveError && <div className="text-xs text-error bg-error-light p-2 rounded">{saveError}</div>}
      <div className="flex items-center justify-end gap-2 pt-2">
        <button type="button" onClick={onCancel} className="px-3 py-1.5 text-xs rounded border">
          <X className="inline w-3 h-3 mr-1" />取消
        </button>
        <button
          type="button"
          onClick={save}
          disabled={saving}
          className="px-3 py-1.5 text-xs rounded bg-accent text-text-on-accent disabled:opacity-50"
        >
          {saving ? <Loader2 className="inline w-3 h-3 mr-1 animate-spin" /> : <Check className="inline w-3 h-3 mr-1" />}
          {saving ? '保存中...' : '保存'}
        </button>
      </div>
    </div>
  );
}

interface FieldProps {
  nickname: string;
  onNicknameChange: (value: string) => void;
  showRole: boolean;
  orgRole: 'admin' | 'member';
  onRoleChange: (value: 'admin' | 'member') => void;
  posCode: PositionCode;
  onPositionChange: (value: PositionCode) => void;
  positions: OrgPosition[];
  deptId: string;
  onDepartmentChange: (value: string) => void;
  departments: OrgDepartment[];
  jobTitle: string;
  onJobTitleChange: (value: string) => void;
  dataScope: DataScope;
  onDataScopeChange: (value: DataScope) => void;
}

function MemberEditorFields(props: FieldProps) {
  return (
    <>
      <div>
        <label className="block text-xs font-medium text-text-secondary mb-1">企业内显示名</label>
        <input
          aria-label="企业内显示名"
          value={props.nickname}
          maxLength={50}
          onChange={(event) => props.onNicknameChange(event.target.value)}
          className="w-full px-3 py-2 border rounded-lg text-sm bg-surface-card"
        />
      </div>
      {props.showRole && (
        <div>
          <label className="block text-xs font-medium text-text-secondary mb-1">企业角色</label>
          <select
            aria-label="企业角色"
            value={props.orgRole}
            onChange={(event) => props.onRoleChange(event.target.value as 'admin' | 'member')}
            className="w-full px-3 py-2 border rounded-lg text-sm bg-surface-card"
          >
            <option value="member">成员</option>
            <option value="admin">管理员</option>
          </select>
        </div>
      )}
      <div>
        <label className="block text-xs font-medium text-text-secondary mb-1">职位</label>
        <select
          value={props.posCode}
          onChange={(event) => props.onPositionChange(event.target.value as PositionCode)}
          className="w-full px-3 py-1.5 text-sm rounded border border-default bg-surface-card"
        >
          {props.positions.map((position) => (
            <option key={position.code} value={position.code}>
              {POSITION_LABELS[position.code]}
            </option>
          ))}
        </select>
      </div>
      {props.posCode !== 'boss' && props.posCode !== 'vp' && (
        <div>
          <label className="block text-xs font-medium text-text-secondary mb-1">部门 *</label>
          <select
            value={props.deptId}
            onChange={(event) => props.onDepartmentChange(event.target.value)}
            className="w-full px-3 py-1.5 text-sm rounded border border-default bg-surface-card"
          >
            <option value="">请选择部门</option>
            {props.departments.map((department) => (
              <option key={department.id} value={department.id}>
                {department.name} ({DEPT_TYPE_LABELS[department.type]})
              </option>
            ))}
          </select>
        </div>
      )}
      <div>
        <label className="block text-xs font-medium text-text-secondary mb-1">自定义头衔（可选）</label>
        <input
          value={props.jobTitle}
          onChange={(event) => props.onJobTitleChange(event.target.value)}
          maxLength={50}
          className="w-full px-3 py-1.5 text-sm rounded border border-default bg-surface-card"
        />
      </div>
      <div>
        <label className="block text-xs font-medium text-text-secondary mb-1">数据范围</label>
        <select
          value={props.dataScope}
          onChange={(event) => props.onDataScopeChange(event.target.value as DataScope)}
          className="w-full px-3 py-1.5 text-sm rounded border border-default bg-surface-card"
        >
          <option value="self">仅自己</option>
          <option value="dept_subtree">本部门</option>
          <option value="all">全公司</option>
        </select>
      </div>
    </>
  );
}
