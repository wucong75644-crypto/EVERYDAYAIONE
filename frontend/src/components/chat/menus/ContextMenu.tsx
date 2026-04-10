/**
 * 右键上下文菜单组件
 */

interface ContextMenuProps {
  x: number;
  y: number;
  closing?: boolean;
  onRename: () => void;
  onDelete: () => void;
}

export default function ContextMenu({
  x,
  y,
  closing = false,
  onRename,
  onDelete,
}: ContextMenuProps) {
  return (
    <div
      className={`fixed bg-surface-card rounded-lg shadow-lg border border-border-default py-1 z-30 min-w-32 ${
        closing ? 'animate-dropdown-exit' : 'animate-dropdown-enter'
      }`}
      style={{ left: `${x}px`, top: `${y}px` }}
      onClick={(e) => e.stopPropagation()}
    >
      <button
        onClick={onRename}
        className="w-full px-4 py-2 text-left text-sm text-text-secondary hover:bg-hover hover:text-text-primary transition-base"
      >
        重命名
      </button>
      <button
        onClick={onDelete}
        className="w-full px-4 py-2 text-left text-sm text-error hover:bg-error-light transition-base"
      >
        删除
      </button>
    </div>
  );
}
