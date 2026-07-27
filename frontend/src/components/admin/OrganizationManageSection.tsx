import { useState } from 'react';
import { MemberAssignmentsSection } from './MemberAssignmentsSection';
import GroupList from '../settings/GroupList';

interface Props {
  orgId: string;
}

type View = 'members' | 'groups';

export default function OrganizationManageSection({ orgId }: Props) {
  const [view, setView] = useState<View>('members');

  return (
    <section className="space-y-4">
      <div className="flex border-b border-[var(--s-border-default)]">
        {([
          { key: 'members' as View, label: '成员与任职' },
          { key: 'groups' as View, label: '群聊管理' },
        ]).map((item) => (
          <button
            key={item.key}
            type="button"
            onClick={() => setView(item.key)}
            className={`px-4 py-2.5 text-sm font-medium border-b-2 transition-colors ${
              view === item.key
                ? 'border-[var(--s-accent)] text-[var(--s-accent)]'
                : 'border-transparent text-[var(--s-text-secondary)] hover:text-[var(--s-text-primary)]'
            }`}
          >
            {item.label}
          </button>
        ))}
      </div>

      {view === 'members' && <MemberAssignmentsSection orgId={orgId} />}
      {view === 'groups' && <GroupList />}
    </section>
  );
}
