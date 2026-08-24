import Modal from '../common/Modal';
import { Button } from '../ui/Button';

interface WorkspaceDeleteDialogProps {
  target: string | null;
  loading: boolean;
  onClose: () => void;
  onConfirm: () => void;
}

export default function WorkspaceDeleteDialog({
  target,
  loading,
  onClose,
  onConfirm,
}: WorkspaceDeleteDialogProps) {
  const displayName = target?.startsWith('batch:')
    ? `${target.split(':')[1]} 个文件`
    : target?.split('/').pop();

  return (
    <Modal isOpen={Boolean(target)} onClose={onClose} title="确认删除" maxWidth="sm">
      <p className="text-sm text-[var(--s-text-secondary)] mb-4">
        确定删除 <span className="font-medium text-[var(--s-text-primary)]">{displayName}</span> 吗？此操作无法撤销。
      </p>
      <div className="flex justify-end gap-2">
        <Button variant="ghost" size="sm" onClick={onClose}>取消</Button>
        <Button variant="danger" size="sm" loading={loading} onClick={onConfirm}>删除</Button>
      </div>
    </Modal>
  );
}
