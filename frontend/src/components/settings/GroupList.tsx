/**
 * 群聊列表 — 显示企业被收集的所有群
 *
 * 列：群名 / chatid / 最后活跃 / 消息数 / [编辑]
 * 点击编辑 → 弹出 EditGroupNameModal 改群名
 *
 * 数据来源：GET /api/wecom-chat-targets/groups
 * 权限：仅 boss/vp 可见此 tab（OrganizationModal 已做判断）
 */
import { useEffect, useState, useCallback } from 'react';
import { Loader2, Pencil, RefreshCw, MessageSquare } from 'lucide-react';
import { wecomChatTargetsService } from '../../services/wecomChatTargets';
import { logger } from '../../utils/logger';
import { cn } from '../../utils/cn';
import EditGroupNameModal from './EditGroupNameModal';
import type { WecomGroup } from '../../types/wecomChatTargets';

function formatTime(iso: string | null): string {
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    const now = new Date();
    const diffMs = now.getTime() - d.getTime();
    const diffHours = diffMs / (1000 * 60 * 60);
    const diffDays = diffHours / 24;

    if (diffHours < 1) return '刚刚';
    if (diffHours < 24) return `${Math.floor(diffHours)} 小时前`;
    if (diffDays < 7) return `${Math.floor(diffDays)} 天前`;
    return d.toLocaleDateString('zh-CN');
  } catch {
    return iso;
  }
}

function shortenChatid(chatid: string, len = 12): string {
  if (chatid.length <= len) return chatid;
  return chatid.slice(0, len) + '...';
}

export default function GroupList() {
  const [groups, setGroups] = useState<WecomGroup[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<WecomGroup | null>(null);

  const loadAll = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await wecomChatTargetsService.listGroups();
      setGroups(data);
    } catch (e) {
      logger.error('group-list', '加载失败', e);
      setError('加载失败，请重试');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  const handleSaved = () => {
    setEditing(null);
    loadAll();
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-16 text-[var(--s-text-tertiary)]">
        <Loader2 className="w-5 h-5 animate-spin mr-2" />
        加载中...
      </div>
    );
  }

  if (error) {
    return (
      <div className="text-center py-16">
        <p className="text-[var(--s-error)] mb-3">{error}</p>
        <button
          type="button"
          onClick={loadAll}
          className="text-sm text-[var(--s-accent)] hover:underline"
        >
          重试
        </button>
      </div>
    );
  }

  if (groups.length === 0) {
    return (
      <div className="text-center py-16 text-[var(--s-text-tertiary)] text-sm">
        还没有收集到任何群聊<br />
        <span className="text-xs">在群里 @机器人 发消息后会自动出现在此</span>
      </div>
    );
  }

  return (
    <div>
      {/* 工具栏 */}
      <div className="flex items-center justify-between mb-3 px-1">
        <span className="text-xs text-[var(--s-text-tertiary)]">
          共 {groups.length} 个群（企微 API 拿不到群名，需手动标注）
        </span>
        <button
          type="button"
          onClick={loadAll}
          className="flex items-center gap-1 text-xs text-[var(--s-text-secondary)] hover:text-[var(--s-text-primary)]"
        >
          <RefreshCw className="w-3 h-3" />
          刷新
        </button>
      </div>

      {/* 表格 */}
      <div className="border border-[var(--s-border-default)] rounded-md overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-[var(--s-surface-sunken)] text-[var(--s-text-tertiary)] text-xs">
            <tr>
              <th className="text-left px-3 py-2 font-medium">群名</th>
              <th className="text-left px-3 py-2 font-medium">chatid</th>
              <th className="text-left px-3 py-2 font-medium">最后活跃</th>
              <th className="text-left px-3 py-2 font-medium">消息数</th>
              <th className="text-right px-3 py-2 font-medium w-16">操作</th>
            </tr>
          </thead>
          <tbody>
            {groups.map((g) => (
              <tr
                key={g.id}
                className="border-t border-[var(--s-border-default)] hover:bg-[var(--s-hover)]"
              >
                <td className="px-3 py-2.5">
                  <div className="flex items-center gap-2">
                    <MessageSquare className="w-4 h-4 text-[var(--s-text-tertiary)]" />
                    {g.chat_name ? (
                      <span className="text-[var(--s-text-primary)] font-medium">
                        {g.chat_name}
                      </span>
                    ) : (
                      <span className="text-[var(--s-text-tertiary)] italic">
                        未命名
                      </span>
                    )}
                  </div>
                </td>
                <td className="px-3 py-2.5">
                  <code className="text-xs text-[var(--s-text-tertiary)]">
                    {shortenChatid(g.chatid)}
                  </code>
                </td>
                <td className="px-3 py-2.5 text-[var(--s-text-tertiary)] text-xs">
                  {formatTime(g.last_active)}
                </td>
                <td className="px-3 py-2.5 text-[var(--s-text-secondary)]">
                  {g.message_count}
                </td>
                <td className="px-3 py-2.5 text-right">
                  <button
                    type="button"
                    onClick={() => setEditing(g)}
                    className={cn(
                      'inline-flex items-center gap-1 px-2 py-1 rounded',
                      'text-xs text-[var(--s-text-secondary)]',
                      'hover:bg-[var(--s-accent-soft)] hover:text-[var(--s-accent)]',
                      'transition-colors',
                    )}
                  >
                    <Pencil className="w-3 h-3" />
                    编辑
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* 编辑子 Modal */}
      {editing && (
        <EditGroupNameModal
          group={editing}
          onClose={() => setEditing(null)}
          onSaved={handleSaved}
        />
      )}
    </div>
  );
}
