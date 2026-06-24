/**
 * 文字右键上下文菜单
 *
 * 在用户消息文字气泡上右键弹出：
 * - 引用：把全文或当前选中片段以 Markdown blockquote 形式插入到输入框
 * - 复制：把全文或当前选中片段复制到剪贴板
 *
 * 壳逻辑（位置/ESC/外部关闭/样式）走 BaseContextMenu，这里只负责拼业务回调。
 */

import { Quote, Copy } from 'lucide-react';
import toast from 'react-hot-toast';
import BaseContextMenu, { type ContextMenuItem } from './BaseContextMenu';

interface TextContextMenuProps {
  x: number;
  y: number;
  /** 完整气泡文字（无选区时使用） */
  fullText: string;
  /** 当前选区文字（trim 后非空才生效） */
  selectedText: string;
  messageId: string;
  closing?: boolean;
  onClose: () => void;
}

export default function TextContextMenu({
  x,
  y,
  fullText,
  selectedText,
  messageId,
  closing = false,
  onClose,
}: TextContextMenuProps) {
  // 有选中片段就引用/复制片段，否则用全文
  const effectiveText = selectedText.trim() ? selectedText : fullText;

  const handleQuote = () => {
    if (effectiveText.trim()) {
      window.dispatchEvent(
        new CustomEvent('chat:quote-text', {
          detail: { text: effectiveText, messageId },
        }),
      );
    }
    onClose();
  };

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(effectiveText);
      toast.success('已复制到剪贴板');
    } catch {
      toast.error('复制失败');
    }
    onClose();
  };

  const items: ContextMenuItem[] = [
    { label: '引用', icon: Quote, onClick: handleQuote, tone: 'accent' },
    { label: '复制', icon: Copy, onClick: handleCopy, tone: 'secondary' },
  ];

  return <BaseContextMenu x={x} y={y} items={items} closing={closing} onClose={onClose} />;
}
