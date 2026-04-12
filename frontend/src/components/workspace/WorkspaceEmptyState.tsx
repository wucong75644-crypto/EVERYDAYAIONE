/**
 * 工作区空状态
 */

import { FolderOpen } from 'lucide-react';

export default function WorkspaceEmptyState() {
  return (
    <div className="flex-1 flex flex-col items-center justify-center py-20 text-center">
      <FolderOpen className="w-16 h-16 text-[var(--s-text-tertiary)] mb-4" strokeWidth={1} />
      <p className="text-[var(--s-text-secondary)] text-base mb-1">
        还没有文件
      </p>
      <p className="text-[var(--s-text-tertiary)] text-sm">
        上传文件或让 AI 帮你创建
      </p>
    </div>
  );
}
