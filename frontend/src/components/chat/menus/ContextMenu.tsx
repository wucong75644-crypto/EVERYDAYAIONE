/**
 * 会话项右键上下文菜单
 *
 * 在会话列表项上右键弹出：
 * - 重命名
 * - 删除
 *
 * 壳逻辑（位置/ESC/外部关闭/样式）走 BaseContextMenu。
 */

import BaseContextMenu, { type ContextMenuItem } from './BaseContextMenu';

interface ContextMenuProps {
  x: number;
  y: number;
  closing?: boolean;
  onRename: () => void;
  onDelete: () => void;
  onClose?: () => void;
}

export default function ContextMenu({
  x,
  y,
  closing = false,
  onRename,
  onDelete,
  onClose,
}: ContextMenuProps) {
  const items: ContextMenuItem[] = [
    { label: '重命名', onClick: onRename, tone: 'secondary' },
    { label: '删除', onClick: onDelete, tone: 'danger' },
  ];

  return (
    <BaseContextMenu
      x={x}
      y={y}
      items={items}
      closing={closing}
      onClose={onClose ?? (() => {})}
    />
  );
}
