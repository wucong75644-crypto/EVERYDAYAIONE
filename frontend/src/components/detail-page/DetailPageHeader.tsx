import { ArrowLeft, Sparkles, UserRound } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { useAuthStore } from '../../stores/useAuthStore';
import { Button } from '../ui/Button';

export function DetailPageHeader() {
  const navigate = useNavigate();
  const user = useAuthStore((state) => state.user);

  const handleBack = () => {
    if (window.history.length > 1) {
      navigate(-1);
      return;
    }
    navigate('/chat');
  };

  return (
    <header className="h-16 px-4 sm:px-6 border-b border-[var(--s-border-default)] bg-[var(--s-surface-card)] flex items-center justify-between gap-4">
      <div className="flex items-center gap-3 min-w-0">
        <div className="w-9 h-9 rounded-[var(--s-radius-control)] bg-[var(--c-button-primary-bg)] text-[var(--c-button-primary-fg)] flex items-center justify-center shrink-0">
          <Sparkles className="w-5 h-5" aria-hidden="true" />
        </div>
        <div className="flex items-center gap-2 min-w-0">
          <p className="font-semibold text-[var(--s-text-primary)] truncate">每日AI</p>
          <span className="h-4 w-px bg-[var(--s-border-default)] shrink-0" aria-hidden="true" />
          <p className="text-sm text-[var(--s-text-secondary)] truncate">主图详情</p>
        </div>
      </div>
      <div className="flex items-center gap-2 sm:gap-4">
        <Button variant="ghost" size="sm" icon={<ArrowLeft className="w-4 h-4" />} onClick={handleBack}>
          返回聊天
        </Button>
        <span className="hidden sm:inline text-sm text-[var(--s-text-secondary)]">
          剩余积分：<strong className="text-[var(--s-text-primary)]">{user?.credits ?? 0}</strong>
        </span>
        <div className="w-8 h-8 rounded-full bg-[var(--s-surface-secondary)] text-[var(--s-text-secondary)] flex items-center justify-center" aria-label={user?.nickname || '用户'}>
          {user?.nickname?.charAt(0) || <UserRound className="w-4 h-4" aria-hidden="true" />}
        </div>
      </div>
    </header>
  );
}
