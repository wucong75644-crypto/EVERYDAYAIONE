/**
 * 空状态组件
 *
 * 显示无对话或无消息时的提示界面
 */

import { MessageSquare } from 'lucide-react';

interface EmptyStateProps {
  /** 是否有选中的对话ID */
  hasConversation: boolean;
}

export default function EmptyState({ hasConversation }: EmptyStateProps) {
  if (!hasConversation) {
    return (
      <div className="flex-1 flex items-center justify-center bg-surface-card">
        <div className="text-center max-w-md px-4">
          <div className="w-16 h-16 bg-accent-light rounded-full flex items-center justify-center mx-auto mb-4">
            <MessageSquare className="w-8 h-8 text-accent" />
          </div>
          <h2 className="text-xl font-semibold text-text-primary mb-2">开始新对话</h2>
          <p className="text-text-tertiary text-sm">选择左侧对话或创建新对话开始聊天</p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 flex items-center justify-center bg-surface-card">
      <div className="text-center max-w-md px-4">
        <div className="w-16 h-16 bg-hover rounded-full flex items-center justify-center mx-auto mb-4">
          <MessageSquare className="w-8 h-8 text-text-disabled" />
        </div>
        <h2 className="text-xl font-semibold text-text-primary mb-2">暂无消息</h2>
        <p className="text-text-tertiary text-sm">在下方输入框发送第一条消息开始对话</p>
      </div>
    </div>
  );
}
