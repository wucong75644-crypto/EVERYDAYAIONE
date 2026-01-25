/**
 * 消息工具栏组件
 */

import { Volume2, ThumbsUp, ThumbsDown, Copy, Share2, Trash2 } from 'lucide-react';

interface MessageToolbarProps {
  messageId: string;
  visible: boolean;
  onDelete: (messageId: string) => void;
}

export default function MessageToolbar({
  messageId,
  visible,
  onDelete,
}: MessageToolbarProps) {
  // 复制消息内容
  const handleCopy = async () => {
    try {
      const messageElement = document.querySelector(`[data-message-id="${messageId}"]`);
      const textContent = messageElement?.textContent || '';
      await navigator.clipboard.writeText(textContent);
    } catch (error) {
      console.error('复制失败:', error);
    }
  };

  // 朗读消息
  const handleSpeak = () => {
    // 暂不支持
  };

  // 点赞
  const handleLike = () => {
    // 暂不支持
  };

  // 点踩
  const handleDislike = () => {
    // 暂不支持
  };

  // 分享
  const handleShare = async () => {
    try {
      const messageElement = document.querySelector(`[data-message-id="${messageId}"]`);
      const textContent = messageElement?.textContent || '';
      await navigator.clipboard.writeText(textContent);
    } catch (error) {
      console.error('分享失败:', error);
    }
  };

  return (
    <div
      className={`
        flex items-center justify-center gap-3 px-3 py-2
        bg-white/95 backdrop-blur-sm rounded-lg shadow-md border border-gray-200
        transition-opacity duration-300
        ${visible ? 'opacity-100' : 'opacity-0 pointer-events-none'}
      `}
    >
      {/* 朗读 */}
      <button
        onClick={handleSpeak}
        className="p-1.5 text-gray-600 hover:text-gray-900 hover:bg-gray-100 rounded-lg transition-colors"
        title="朗读"
      >
        <Volume2 className="w-5 h-5" />
      </button>

      {/* 点赞 */}
      <button
        onClick={handleLike}
        className="p-1.5 text-gray-600 hover:text-gray-900 hover:bg-gray-100 rounded-lg transition-colors"
        title="点赞"
      >
        <ThumbsUp className="w-5 h-5" />
      </button>

      {/* 点踩 */}
      <button
        onClick={handleDislike}
        className="p-1.5 text-gray-600 hover:text-gray-900 hover:bg-gray-100 rounded-lg transition-colors"
        title="点踩"
      >
        <ThumbsDown className="w-5 h-5" />
      </button>

      {/* 复制 */}
      <button
        onClick={handleCopy}
        className="p-1.5 text-gray-600 hover:text-gray-900 hover:bg-gray-100 rounded-lg transition-colors"
        title="复制"
      >
        <Copy className="w-5 h-5" />
      </button>

      {/* 分享 */}
      <button
        onClick={handleShare}
        className="p-1.5 text-gray-600 hover:text-gray-900 hover:bg-gray-100 rounded-lg transition-colors"
        title="分享"
      >
        <Share2 className="w-5 h-5" />
      </button>

      {/* 删除（危险操作，hover时变红） */}
      <button
        onClick={() => onDelete(messageId)}
        className="p-1.5 text-gray-600 hover:text-red-500 hover:bg-red-50 rounded-lg transition-colors"
        title="删除"
      >
        <Trash2 className="w-5 h-5" />
      </button>
    </div>
  );
}
