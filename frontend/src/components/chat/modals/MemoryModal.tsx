/**
 * 记忆管理弹窗
 *
 * 显示记忆列表、添加记忆、全局开关。
 */

import { useState, useRef } from 'react';
import { Plus, Trash2, Search, Brain } from 'lucide-react';
import Modal from '../../common/Modal';
import MemoryItem from './MemoryItem';
import { useMemoryStore } from '../../../stores/useMemoryStore';

export default function MemoryModal() {
  const {
    memories,
    loading,
    operating,
    error,
    settings,
    settingsLoading,
    isModalOpen,
    searchQuery,
    closeModal,
    setSearchQuery,
    addMemory,
    updateMemory,
    deleteMemory,
    deleteAllMemories,
    toggleMemoryEnabled,
  } = useMemoryStore();

  const [addContent, setAddContent] = useState('');
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const handleAdd = async () => {
    const trimmed = addContent.trim();
    if (!trimmed) return;

    const ok = await addMemory(trimmed);
    if (ok) {
      setAddContent('');
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      handleAdd();
    }
  };

  const handleDeleteAll = async () => {
    await deleteAllMemories();
    setShowDeleteConfirm(false);
  };

  // 过滤记忆
  const filteredMemories = searchQuery
    ? memories.filter((m) =>
        m.memory.toLowerCase().includes(searchQuery.toLowerCase())
      )
    : memories;

  const memoryEnabled = settings?.memory_enabled ?? true;

  return (
    <Modal
      isOpen={isModalOpen}
      onClose={closeModal}
      maxWidth="max-w-lg"
      showCloseButton={false}
    >
      {/* 头部 */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <Brain className="w-5 h-5 text-accent" />
          <h2 className="text-lg font-semibold text-text-primary">AI 记忆</h2>
          <span className="text-xs text-text-disabled bg-hover px-1.5 py-0.5 rounded">
            {memories.length} 条
          </span>
        </div>

        {/* 开关 */}
        <button
          onClick={toggleMemoryEnabled}
          disabled={settingsLoading}
          className="relative inline-flex h-6 w-11 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-base duration-200 ease-in-out focus:outline-none"
          style={{
            backgroundColor: memoryEnabled ? '#3b82f6' : '#d1d5db',
          }}
          role="switch"
          aria-checked={memoryEnabled}
          aria-label="记忆功能开关"
        >
          <span
            className={`pointer-events-none inline-block h-5 w-5 transform rounded-full bg-surface-card shadow ring-0 transition duration-200 ease-in-out ${
              memoryEnabled ? 'translate-x-5' : 'translate-x-0'
            }`}
          />
        </button>
      </div>

      {!memoryEnabled && (
        <div className="mb-4 px-3 py-2 bg-surface rounded-lg text-sm text-text-tertiary">
          记忆功能已关闭，AI 不会记住对话信息。
        </div>
      )}

      {memoryEnabled && (
        <>
          {/* 搜索栏 */}
          {memories.length > 5 && (
            <div className="relative mb-3">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-text-disabled" />
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="搜索记忆..."
                className="w-full pl-9 pr-3 py-2 text-sm border border-border-default rounded-lg focus:outline-none focus:ring-2 focus:ring-focus-ring focus:border-transparent"
              />
            </div>
          )}

          {/* 添加记忆 */}
          <div className="flex items-center gap-2 mb-3">
            <input
              ref={inputRef}
              type="text"
              value={addContent}
              onChange={(e) => setAddContent(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="手动添加记忆..."
              maxLength={500}
              disabled={operating}
              className="flex-1 px-3 py-2 text-sm border border-border-default rounded-lg focus:outline-none focus:ring-2 focus:ring-focus-ring focus:border-transparent disabled:opacity-50"
            />
            <button
              onClick={handleAdd}
              disabled={!addContent.trim() || operating}
              className="p-2 text-text-on-accent bg-accent hover:bg-accent-hover rounded-lg transition-base disabled:opacity-50 disabled:cursor-not-allowed flex-shrink-0"
              aria-label="添加记忆"
            >
              <Plus className="w-4 h-4" />
            </button>
          </div>

          {/* 错误提示 */}
          {error && (
            <div className="mb-3 px-3 py-2 bg-error-light text-error text-sm rounded-lg">
              {error}
            </div>
          )}

          {/* 记忆列表 */}
          <div className="max-h-80 overflow-y-auto -mx-5 px-5">
            {loading ? (
              <div className="py-8 text-center text-sm text-text-disabled">
                加载中...
              </div>
            ) : filteredMemories.length === 0 ? (
              <div className="py-8 text-center text-sm text-text-disabled">
                {searchQuery
                  ? '没有找到匹配的记忆'
                  : '暂无记忆，AI 会在对话中自动提取关键信息'}
              </div>
            ) : (
              <div className="space-y-0.5">
                {filteredMemories.map((m) => (
                  <MemoryItem
                    key={m.id}
                    memory={m}
                    onUpdate={updateMemory}
                    onDelete={deleteMemory}
                    disabled={operating}
                  />
                ))}
              </div>
            )}
          </div>

          {/* 底部操作 */}
          {memories.length > 0 && (
            <div className="mt-3 pt-3 border-t border-border-light">
              {showDeleteConfirm ? (
                <div className="flex items-center justify-between">
                  <span className="text-sm text-error">确定清空所有记忆？</span>
                  <div className="flex items-center gap-2">
                    <button
                      onClick={() => setShowDeleteConfirm(false)}
                      className="px-3 py-1.5 text-sm text-text-tertiary hover:bg-hover rounded-lg transition-base"
                    >
                      取消
                    </button>
                    <button
                      onClick={handleDeleteAll}
                      disabled={operating}
                      className="px-3 py-1.5 text-sm text-text-on-accent bg-error hover:bg-error/90 rounded-lg transition-base disabled:opacity-50"
                    >
                      确定清空
                    </button>
                  </div>
                </div>
              ) : (
                <button
                  onClick={() => setShowDeleteConfirm(true)}
                  className="flex items-center gap-1.5 text-sm text-text-disabled hover:text-error transition-base"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                  清空所有记忆
                </button>
              )}
            </div>
          )}
        </>
      )}
    </Modal>
  );
}
