/**
 * 单条记忆展示组件
 *
 * 支持查看、编辑、删除三种状态。
 */

import { useState, useRef, useEffect } from 'react';
import { Edit2, Trash2, Check, X } from 'lucide-react';
import type { MemoryItem as MemoryItemType } from '../../../services/memory';

interface MemoryItemProps {
  memory: MemoryItemType;
  onUpdate: (id: string, content: string) => Promise<boolean>;
  onDelete: (id: string) => Promise<boolean>;
  disabled?: boolean;
}

export default function MemoryItem({
  memory,
  onUpdate,
  onDelete,
  disabled = false,
}: MemoryItemProps) {
  const [isEditing, setIsEditing] = useState(false);
  const [editContent, setEditContent] = useState(memory.memory);
  const [isDeleting, setIsDeleting] = useState(false);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (isEditing && inputRef.current) {
      inputRef.current.focus();
      inputRef.current.setSelectionRange(
        inputRef.current.value.length,
        inputRef.current.value.length
      );
    }
  }, [isEditing]);

  const handleSave = async () => {
    const trimmed = editContent.trim();
    if (!trimmed || trimmed === memory.memory) {
      setIsEditing(false);
      setEditContent(memory.memory);
      return;
    }
    const ok = await onUpdate(memory.id, trimmed);
    if (ok) {
      setIsEditing(false);
    }
  };

  const handleCancel = () => {
    setIsEditing(false);
    setEditContent(memory.memory);
  };

  const handleDelete = async () => {
    setIsDeleting(true);
    const deleted = await onDelete(memory.id);
    // 仅在删除失败时重置（成功时组件会从列表移除，无需 setState）
    if (!deleted) {
      setIsDeleting(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSave();
    }
    if (e.key === 'Escape') {
      handleCancel();
    }
  };

  const sourceLabel = memory.metadata.source === 'auto' ? 'AI 提取' : '手动添加';
  const sourceColor =
    memory.metadata.source === 'auto'
      ? 'text-accent bg-accent-light'
      : 'text-success bg-success-light';

  return (
    <div className="group px-3 py-2.5 hover:bg-surface rounded-lg transition-base">
      {isEditing ? (
        <div className="space-y-2">
          <textarea
            ref={inputRef}
            value={editContent}
            onChange={(e) => setEditContent(e.target.value)}
            onKeyDown={handleKeyDown}
            maxLength={500}
            rows={2}
            className="w-full px-3 py-2 text-sm border border-border-default rounded-lg resize-none focus:outline-none focus:ring-2 focus:ring-focus-ring focus:border-transparent"
          />
          <div className="flex items-center justify-between">
            <span className="text-xs text-text-disabled">
              {editContent.length}/500
            </span>
            <div className="flex items-center gap-1.5">
              <button
                onClick={handleCancel}
                className="p-1.5 text-text-disabled hover:text-text-tertiary hover:bg-hover rounded-lg transition-base"
                aria-label="取消"
              >
                <X className="w-4 h-4" />
              </button>
              <button
                onClick={handleSave}
                disabled={!editContent.trim() || disabled}
                className="p-1.5 text-accent hover:bg-accent-light rounded-lg transition-base disabled:opacity-50"
                aria-label="保存"
              >
                <Check className="w-4 h-4" />
              </button>
            </div>
          </div>
        </div>
      ) : (
        <div className="flex items-start gap-2">
          <div className="flex-1 min-w-0">
            <p className="text-sm text-text-secondary leading-relaxed break-words">
              {memory.memory}
            </p>
            <div className="mt-1 flex items-center gap-2">
              <span
                className={`text-xs px-1.5 py-0.5 rounded ${sourceColor}`}
              >
                {sourceLabel}
              </span>
              {memory.created_at && (
                <span className="text-xs text-text-disabled">
                  {new Date(memory.created_at).toLocaleDateString('zh-CN')}
                </span>
              )}
            </div>
          </div>
          <div className="flex items-center gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity flex-shrink-0">
            <button
              onClick={() => setIsEditing(true)}
              disabled={disabled}
              className="p-1.5 text-text-disabled hover:text-accent hover:bg-accent-light rounded-lg transition-base"
              aria-label="编辑"
            >
              <Edit2 className="w-3.5 h-3.5" />
            </button>
            <button
              onClick={handleDelete}
              disabled={disabled || isDeleting}
              className="p-1.5 text-text-disabled hover:text-error hover:bg-error-light rounded-lg transition-base"
              aria-label="删除"
            >
              <Trash2 className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
