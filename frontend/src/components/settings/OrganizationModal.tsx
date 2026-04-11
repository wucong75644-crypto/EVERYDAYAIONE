/**
 * 组织管理 Modal — 含 tab 切换（员工 / 群聊）
 *
 * 业务规则：
 * - 入口已经做了 sys.member.edit 权限检查，到这里的人都是管理员
 * - 群聊 tab 仅 boss/vp 可见（manager 看不到，避免普通主管管群聊）
 *   理由：用户原话"群聊 tab 在没设置到管理的时候不显示，只有管理显示"
 *   我们用更严格的判定 — boss/vp 才显示群聊 tab
 *
 * P3 阶段只实现员工 tab，群聊 tab 在 P4 实现内容（占位 placeholder）
 */
import { useState } from 'react';
import { Users, MessageSquare } from 'lucide-react';
import Modal from '../common/Modal';
import MemberList from './MemberList';
import GroupList from './GroupList';
import { useCurrentMember } from '../../hooks/usePermission';
import { cn } from '../../utils/cn';

interface Props {
  isOpen: boolean;
  onClose: () => void;
}

type TabKey = 'members' | 'groups';

export default function OrganizationModal({ isOpen, onClose }: Props) {
  const [tab, setTab] = useState<TabKey>('members');
  const member = useCurrentMember();

  // 群聊 tab 仅 boss/vp 可见
  const canManageGroups = member?.position_code === 'boss' || member?.position_code === 'vp';

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title="组织管理"
      maxWidth="max-w-4xl"
    >
      {/* Tab 导航 */}
      <div className="flex border-b border-[var(--s-border-default)] -mt-3 -mx-1 mb-4">
        <TabButton
          active={tab === 'members'}
          onClick={() => setTab('members')}
          icon={<Users className="w-4 h-4" />}
          label="员工管理"
        />
        {canManageGroups && (
          <TabButton
            active={tab === 'groups'}
            onClick={() => setTab('groups')}
            icon={<MessageSquare className="w-4 h-4" />}
            label="群聊管理"
          />
        )}
      </div>

      {/* Tab 内容 */}
      <div className="min-h-[400px]">
        {tab === 'members' && <MemberList />}
        {tab === 'groups' && canManageGroups && <GroupList />}
      </div>
    </Modal>
  );
}

interface TabButtonProps {
  active: boolean;
  onClick: () => void;
  icon: React.ReactNode;
  label: string;
}

function TabButton({ active, onClick, icon, label }: TabButtonProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'flex items-center gap-2 px-4 py-2.5 text-sm font-medium',
        'border-b-2 transition-colors',
        active
          ? 'border-[var(--s-accent)] text-[var(--s-accent)]'
          : 'border-transparent text-[var(--s-text-secondary)] hover:text-[var(--s-text-primary)]',
      )}
    >
      {icon}
      {label}
    </button>
  );
}
