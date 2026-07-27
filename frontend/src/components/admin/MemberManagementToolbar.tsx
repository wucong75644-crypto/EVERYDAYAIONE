import { useState } from 'react';
import { createInvitation } from '../../services/org';

export type WecomMemberFilter = 'all' | 'linked' | 'unlinked';

interface Props {
  orgId: string;
  memberCount: number;
  filter: WecomMemberFilter;
  filterDisabled: boolean;
  onFilterChange: (value: WecomMemberFilter) => void;
}

export default function MemberManagementToolbar({
  orgId,
  memberCount,
  filter,
  filterDisabled,
  onFilterChange,
}: Props) {
  const [showInvite, setShowInvite] = useState(false);
  const [phone, setPhone] = useState('');
  const [role, setRole] = useState<'admin' | 'member'>('member');
  const [submitting, setSubmitting] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');

  const invite = async () => {
    if (!/^1[3-9]\d{9}$/.test(phone)) {
      setError('请输入正确的手机号');
      return;
    }
    setSubmitting(true);
    setError('');
    setMessage('');
    try {
      await createInvitation(orgId, phone, role);
      setMessage(`已向 ${phone} 发送邀请`);
      setPhone('');
      setShowInvite(false);
    } catch {
      setError('邀请失败，请重试');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <span className="text-sm text-text-tertiary">共 {memberCount} 名成员</span>
          <select
            aria-label="企微关联状态"
            value={filter}
            disabled={filterDisabled}
            onChange={(event) => onFilterChange(event.target.value as WecomMemberFilter)}
            className="px-2 py-1.5 border rounded-lg text-sm bg-surface-card disabled:opacity-50"
          >
            <option value="all">全部企微状态</option>
            <option value="linked">已关联企微</option>
            <option value="unlinked">未关联企微</option>
          </select>
        </div>
        <button
          type="button"
          onClick={() => {
            setShowInvite((value) => !value);
            setError('');
            setMessage('');
          }}
          className="px-3 py-1.5 text-sm bg-accent text-text-on-accent rounded-lg"
        >
          {showInvite ? '取消' : '+ 邀请成员'}
        </button>
      </div>
      {message && <div className="bg-success-light text-success p-2 rounded text-sm">{message}</div>}
      {error && <div className="bg-error-light text-error p-2 rounded text-sm">{error}</div>}
      {showInvite && (
        <div className="flex gap-2 p-3 bg-surface rounded-lg border">
          <input
            aria-label="成员手机号"
            type="tel"
            value={phone}
            onChange={(event) => setPhone(event.target.value)}
            placeholder="输入手机号"
            maxLength={11}
            className="flex-1 px-3 py-1.5 border rounded-lg text-sm"
          />
          <select
            aria-label="邀请角色"
            value={role}
            onChange={(event) => setRole(event.target.value as typeof role)}
            className="px-3 py-1.5 border rounded-lg text-sm bg-surface-card"
          >
            <option value="member">成员</option>
            <option value="admin">管理员</option>
          </select>
          <button
            type="button"
            onClick={invite}
            disabled={submitting || !phone}
            className="px-3 py-1.5 text-sm bg-accent text-text-on-accent rounded-lg disabled:opacity-50"
          >
            {submitting ? '发送中...' : '发送邀请'}
          </button>
        </div>
      )}
    </div>
  );
}
