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
      className={`fixed bg-white dark:bg-gray-800 rounded-lg shadow-lg border border-gray-200 dark:border-gray-700 py-1 z-50 min-w-32 ${
        closing ? 'animate-slideUp' : 'animate-slideDown'
      }`}
      style={{ left: `${x}px`, top: `${y}px` }}
      onClick={(e) => e.stopPropagation()}
    >
      <button
        onClick={onRename}
        className="w-full px-4 py-2 text-left text-sm text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700"
      >
        重命名
      </button>
      <button
        onClick={onDelete}
        className="w-full px-4 py-2 text-left text-sm text-red-600 dark:text-red-400 hover:bg-gray-100 dark:hover:bg-gray-700"
      >
        删除
      </button>
    </div>
  );
}
