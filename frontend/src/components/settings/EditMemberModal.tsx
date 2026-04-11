/**
 * 编辑成员 Modal — 子 modal
 *
 * 编辑字段：
 * - 显示名 (users.nickname) — 单独 PATCH /profile
 * - 部门 / 职位 / 数据范围 — 一起 PATCH /assignment
 *
 * 设计：两个 patch 串行执行，单边失败给具体提示
 */
import { useState } from 'react';
import Modal from '../common/Modal';
import { Input } from '../ui/Input';
import { Button } from '../ui/Button';
import { orgMembersService } from '../../services/orgMembers';
import { logger } from '../../utils/logger';
import { cn } from '../../utils/cn';
import type {
  WecomCollectedMember,
  OrgDepartment,
  OrgPosition,
} from '../../types/orgMembers';
import type { PositionCode, DataScope } from '../../types/auth';

interface Props {
  member: WecomCollectedMember;
  departments: OrgDepartment[];
  positions: OrgPosition[];
  onClose: () => void;
  onSaved: () => void;
}

export default function EditMemberModal({
  member,
  departments,
  positions,
  onClose,
  onSaved,
}: Props) {
  const [nickname, setNickname] = useState(member.nickname);
  const [departmentId, setDepartmentId] = useState<string>(
    member.assignment?.department_id || '',
  );
  const [positionCode, setPositionCode] = useState<PositionCode>(
    member.assignment?.position_code || 'member',
  );
  const [dataScope, setDataScope] = useState<DataScope>(
    member.assignment?.data_scope || 'self',
  );

  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async () => {
    setError(null);
    if (!nickname.trim()) {
      setError('显示名不能为空');
      return;
    }

    setSubmitting(true);
    try {
      // 1. 改 nickname（如有变更）
      if (nickname.trim() !== member.nickname) {
        await orgMembersService.updateProfile(member.user_id, {
          nickname: nickname.trim(),
        });
      }

      // 2. 改 assignment（始终发送，后端会比对）
      await orgMembersService.updateAssignment(member.user_id, {
        department_id: departmentId || undefined,
        position_code: positionCode,
        data_scope: dataScope,
      });

      onSaved();
    } catch (e) {
      logger.error('edit-member', '保存失败', e);
      setError('保存失败，请检查后重试');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      isOpen={true}
      onClose={onClose}
      title={`编辑：${member.nickname}`}
      maxWidth="max-w-md"
    >
      <div className="space-y-4">
        {/* 显示名 */}
        <div>
          <label className="block text-sm font-medium text-[var(--s-text-secondary)] mb-1.5">
            显示名 *
          </label>
          <Input
            value={nickname}
            onChange={(e) => setNickname(e.target.value)}
            placeholder="员工显示名"
          />
          <p className="text-xs text-[var(--s-text-tertiary)] mt-1">
            会覆盖企微同步的真名（如想清洁化"客服部 - 蔡娟"这类）
          </p>
        </div>

        {/* 部门 */}
        <div>
          <label className="block text-sm font-medium text-[var(--s-text-secondary)] mb-1.5">
            部门
          </label>
          <SelectField
            value={departmentId}
            onChange={setDepartmentId}
            options={[
              { value: '', label: '未分配' },
              ...departments.map((d) => ({ value: d.id, label: d.name })),
            ]}
          />
        </div>

        {/* 职位 */}
        <div>
          <label className="block text-sm font-medium text-[var(--s-text-secondary)] mb-1.5">
            职位 *
          </label>
          <SelectField
            value={positionCode}
            onChange={(v) => setPositionCode(v as PositionCode)}
            options={positions.map((p) => ({
              value: p.code,
              label: p.name,
            }))}
          />
        </div>

        {/* 数据范围 */}
        <div>
          <label className="block text-sm font-medium text-[var(--s-text-secondary)] mb-1.5">
            数据范围 *
          </label>
          <SelectField
            value={dataScope}
            onChange={(v) => setDataScope(v as DataScope)}
            options={[
              { value: 'self', label: '仅自己' },
              { value: 'dept_subtree', label: '本部门' },
              { value: 'all', label: '全公司' },
            ]}
          />
          <p className="text-xs text-[var(--s-text-tertiary)] mt-1">
            决定该员工能查看/操作的数据范围（任务、订单等）
          </p>
        </div>

        {error && (
          <div className="text-xs text-[var(--s-error)] bg-[var(--s-error-soft)] px-3 py-2 rounded">
            {error}
          </div>
        )}

        {/* 按钮 */}
        <div className="flex items-center justify-end gap-2 pt-2">
          <Button variant="secondary" size="sm" onClick={onClose}>
            取消
          </Button>
          <Button
            variant="accent"
            size="sm"
            loading={submitting}
            onClick={handleSubmit}
          >
            保存
          </Button>
        </div>
      </div>
    </Modal>
  );
}

interface SelectFieldProps {
  value: string;
  onChange: (v: string) => void;
  options: Array<{ value: string; label: string }>;
}

function SelectField({ value, onChange, options }: SelectFieldProps) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className={cn(
        'w-full px-3 py-2 text-sm rounded-[var(--c-input-radius)]',
        'bg-[var(--c-input-bg)] text-[var(--c-input-fg)]',
        'border border-[var(--c-input-border)]',
        'focus:outline-none focus:border-[var(--c-input-border-focus)]',
        'focus:shadow-[var(--c-input-ring-focus)]',
      )}
    >
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  );
}
