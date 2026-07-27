import type { ReactNode } from 'react';

interface CredentialGroupSectionProps {
  children: ReactNode;
  configured: boolean;
  editing: boolean;
  label: string;
  onEdit: () => void;
}

export default function CredentialGroupSection({
  children,
  configured,
  editing,
  label,
  onEdit,
}: CredentialGroupSectionProps) {
  return (
    <section className="space-y-3 border rounded-lg p-3">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <h4 className="text-sm font-medium text-text-primary">{label}</h4>
          <span className={configured ? 'text-xs text-success' : 'text-xs text-text-disabled'}>
            <span
              aria-hidden="true"
              className={`mr-1.5 inline-block h-2 w-2 rounded-full ${
                configured ? 'bg-success' : 'bg-text-disabled'
              }`}
            />
            {configured ? '已配置' : '未配置'}
          </span>
        </div>
        {configured && !editing && (
          <button
            type="button"
            onClick={onEdit}
            className="px-3 py-1.5 text-sm text-accent border border-accent/30 rounded-lg hover:bg-accent-light transition-base whitespace-nowrap"
          >
            重新配置
          </button>
        )}
      </div>
      {(!configured || editing) && children}
    </section>
  );
}
