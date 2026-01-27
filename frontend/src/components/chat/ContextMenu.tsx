/**
 * 右键上下文菜单组件
 */

interface ContextMenuProps {
  x: number;
  y: number;
  onRename: () => void;
  onDelete: () => void;
}

export default function ContextMenu({
  x,
  y,
  onRename,
  onDelete,
}: ContextMenuProps) {
  return (
    <div
      className="fixed bg-white rounded-lg shadow-lg border border-gray-200 py-1 z-50 min-w-32"
      style={{ left: x, top: y }}
      onClick={(e) => e.stopPropagation()}
    >
      <button
        onClick={onRename}
        className="w-full px-4 py-2 text-left text-sm text-gray-700 hover:bg-gray-100"
      >
        重命名
      </button>
      <button
        onClick={onDelete}
        className="w-full px-4 py-2 text-left text-sm text-red-600 hover:bg-gray-100"
      >
        删除
      </button>
    </div>
  );
}
